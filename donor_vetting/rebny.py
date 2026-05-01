from __future__ import annotations

import re
import sys
import time
import subprocess
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Iterable, Optional

REBNY_MEMBERS_URL = "https://www.rebny.com/members/"

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None
    PlaywrightTimeoutError = Exception


@dataclass(slots=True)
class RebnyResult:
    rebny_status: str
    rebny_match: bool
    rebny_detail: str
    rebny_result_name: str = ""
    rebny_company: str = ""
    rebny_confidence: int = 0
    rebny_query_used: str = ""

    def to_dict(self) -> dict:
        return {
            "rebny_status": self.rebny_status,
            "rebny_match": self.rebny_match,
            "rebny_detail": self.rebny_detail,
            "rebny_result_name": self.rebny_result_name,
            "rebny_company": self.rebny_company,
            "rebny_confidence": self.rebny_confidence,
            "rebny_query_used": self.rebny_query_used,
        }


_COMPANY_WORDS = {
    "llc", "inc", "corp", "corporation", "company", "co", "ltd", "lp", "llp",
    "pllc", "pc", "management", "services", "service", "realty", "estate",
    "brokerage", "commercial", "residential", "owner", "owners", "group",
    "properties", "property", "partners", "associates", "association",
    "development", "capital", "holdings", "advisors", "advisory", "agency",
}
_SKIP_LINES = {
    "member directory", "search by name", "filter & sort", "filter", "sort",
    "no search results found", "contact us", "search", "login", "join us",
    "member resources", "stay connected", "access member resources", "events & education",
    "news & media", "about", "membership", "other", "careers", "faq",
    "terms of use", "privacy policy", "become a member", "residential listing service",
}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "esq", "phd", "md"}


def _strip_accents(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def _clean_text(value: str) -> str:
    value = _strip_accents(value)
    value = value.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _name_tokens(value: str) -> list[str]:
    value = _clean_text(value).lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9' .-]", " ", value)
    value = value.replace(".", "")
    parts = [p.strip("'- ") for p in re.split(r"[\s-]+", value) if p.strip("'- ")]
    return [p for p in parts if p not in _SUFFIXES]


def _normalized_name(value: str) -> str:
    return " ".join(_name_tokens(value))


def _initials(value: str) -> str:
    return "".join(token[0] for token in _name_tokens(value) if token)


def _line_is_probably_company(line: str) -> bool:
    tokens = set(_name_tokens(line))
    return bool(tokens & _COMPANY_WORDS)


def _candidate_lines(page_text: str) -> list[str]:
    lines = []
    for raw in (page_text or "").splitlines():
        line = _clean_text(raw)
        if not line:
            continue
        low = line.lower().strip()
        if low in _SKIP_LINES:
            continue
        if re.fullmatch(r"\d+\s+members?", low):
            continue
        if low.startswith("©") or low.startswith("image:") or "btn_arrow" in low:
            continue
        if len(line) > 90:
            continue
        lines.append(line)
    return lines


def _match_score(first_name: str, last_name: str, candidate: str) -> tuple[str, int]:
    query_full = _normalized_name(f"{first_name} {last_name}")
    cand_norm = _normalized_name(candidate)
    if not query_full or not cand_norm:
        return "none", 0

    q_tokens = query_full.split()
    c_tokens = cand_norm.split()
    if len(q_tokens) < 2 or len(c_tokens) < 2:
        return "none", 0

    q_first = q_tokens[0]
    q_last = q_tokens[-1]
    c_first = c_tokens[0]
    c_last = c_tokens[-1]

    similarity = int(round(100 * SequenceMatcher(None, query_full, cand_norm).ratio()))

    if cand_norm == query_full:
        return "found", 100

    if q_first in c_tokens and q_last in c_tokens:
        return "found", max(96, similarity)

    if c_first == q_first and c_last == q_last:
        return "found", max(95, similarity)

    if q_last == c_last and (c_first == q_first or c_first.startswith(q_first) or q_first.startswith(c_first)):
        return "found", max(93, similarity)

    if q_last == c_last and q_first and c_first and q_first[0] == c_first[0]:
        return "review", max(82, similarity)

    q_initials = _initials(query_full)
    c_initials = _initials(cand_norm)
    if q_last == c_last and q_initials and c_initials and q_initials == c_initials:
        return "found", max(92, similarity)

    if similarity >= 94 and q_last in c_tokens:
        return "found", similarity

    if similarity >= 86 and q_last in c_tokens:
        return "review", similarity

    return "none", similarity


def _parse_member_count(text: str) -> Optional[int]:
    matches = re.findall(r"\b(\d{1,6})\s+Members?\b", text or "", flags=re.I)
    if not matches:
        return None
    try:
        return int(matches[-1])
    except ValueError:
        return None


def _parse_rebny_text(first_name: str, last_name: str, page_text: str, query_used: str = "") -> RebnyResult:
    text = page_text or ""
    lines = _candidate_lines(text)
    count = _parse_member_count(text)
    no_results = "no search results found" in text.lower()

    reviews: list[tuple[str, int]] = []
    for line in lines:
        if _line_is_probably_company(line):
            continue
        quality, score = _match_score(first_name, last_name, line)
        if quality == "found":
            return RebnyResult(
                rebny_status="FOUND",
                rebny_match=True,
                rebny_detail=f"Matched REBNY directory result: {line}",
                rebny_result_name=line,
                rebny_confidence=score,
                rebny_query_used=query_used,
            )
        if quality == "review":
            reviews.append((line, score))

    if reviews:
        name, score = sorted(reviews, key=lambda item: item[1], reverse=True)[0]
        return RebnyResult(
            rebny_status="review",
            rebny_match=False,
            rebny_detail=f"Possible REBNY match, review manually: {name}",
            rebny_result_name=name,
            rebny_confidence=score,
            rebny_query_used=query_used,
        )

    if no_results:
        return RebnyResult(
            rebny_status="not found",
            rebny_match=False,
            rebny_detail="No matching REBNY directory result returned.",
            rebny_confidence=0,
            rebny_query_used=query_used,
        )

    if count and count > 0:
        visible = ", ".join(lines[:5])
        detail = f"REBNY returned {count} result(s), but no exact person-name match was extractable."
        if visible:
            detail += f" Visible text: {visible}"
        return RebnyResult(
            rebny_status="review",
            rebny_match=False,
            rebny_detail=detail,
            rebny_confidence=50,
            rebny_query_used=query_used,
        )

    return RebnyResult(
        rebny_status="unknown",
        rebny_match=False,
        rebny_detail="REBNY page loaded, but no result state was detected.",
        rebny_confidence=0,
        rebny_query_used=query_used,
    )


def _ensure_chromium_installed() -> None:
    if sync_playwright is None:
        return
    try:
        # A no-op when browsers are already installed. Useful on Streamlit Cloud.
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
            check=False,
        )
    except Exception:
        pass


class RebnyDirectoryClient:
    def __init__(self, headless: bool = True):
        if sync_playwright is None:
            raise RuntimeError("playwright is not installed")
        self.headless = headless
        self._playwright = None
        self._browser = None
        self._page = None

    def close(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            try:
                if self._playwright:
                    self._playwright.stop()
            finally:
                self._playwright = None
                self._browser = None
                self._page = None

    def _start(self) -> None:
        if self._page is not None:
            return
        _ensure_chromium_installed()
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = self._browser.new_context(
            viewport={"width": 1440, "height": 1100},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
        )
        self._page = context.new_page()
        self._page.goto(REBNY_MEMBERS_URL, wait_until="domcontentloaded", timeout=45000)
        try:
            self._page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        self._page.wait_for_timeout(1000)

    def _search_input(self):
        page = self._page
        selectors = [
            "xpath=(//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'search by name')]/following::input[not(@type='hidden') and not(@type='submit')][1])",
            "input[placeholder*='Search' i]",
            "input[aria-label*='Search' i]",
            "input[name*='search' i]",
            "input[type='search']",
            "input[type='text']",
            "input:not([type])",
        ]
        for selector in selectors:
            loc = page.locator(selector)
            try:
                for i in range(min(loc.count(), 5)):
                    item = loc.nth(i)
                    if item.is_visible() and item.is_enabled():
                        return item
            except Exception:
                continue
        return None

    def _submit_search(self) -> None:
        page = self._page
        try:
            page.keyboard.press("Enter")
        except Exception:
            pass

        button_selectors = [
            "xpath=(//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'search by name')]/following::button[1])",
            "button[type='submit']",
            "input[type='submit']",
            "button[aria-label*='Search' i]",
        ]
        for selector in button_selectors:
            try:
                loc = page.locator(selector)
                if loc.count() and loc.first.is_visible() and loc.first.is_enabled():
                    loc.first.click(timeout=1500)
                    break
            except Exception:
                continue

    def _set_query(self, query: str) -> bool:
        inp = self._search_input()
        if inp is None:
            return False
        try:
            inp.click(timeout=5000)
            inp.press("Control+A")
            inp.fill(query, timeout=5000)
            # Some JS search widgets only react to DOM events, not just value changes.
            self._page.evaluate(
                """
                (q) => {
                    const visible = (el) => {
                        const r = el.getBoundingClientRect();
                        const s = window.getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                    };
                    const inputs = Array.from(document.querySelectorAll('input'))
                        .filter(el => visible(el) && el.type !== 'hidden' && el.type !== 'submit');
                    const input = inputs.find(el => /search|name/i.test(`${el.name} ${el.id} ${el.placeholder} ${el.ariaLabel}`)) || inputs[0];
                    if (!input) return false;
                    input.focus();
                    input.value = q;
                    input.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: q }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true }));
                    return true;
                }
                """,
                query,
            )
            self._submit_search()
            return True
        except Exception:
            return False

    def search_text(self, query: str) -> str:
        self._start()
        if not self._set_query(query):
            raise RuntimeError("Could not find the REBNY Search By Name input")
        for _ in range(8):
            self._page.wait_for_timeout(500)
            try:
                self._page.wait_for_load_state("networkidle", timeout=1000)
            except Exception:
                pass
        return self._page.locator("body").inner_text(timeout=10000)

    def lookup(self, first_name: str, last_name: str) -> RebnyResult:
        first_name = _clean_text(first_name)
        last_name = _clean_text(last_name)
        if not first_name or not last_name:
            return RebnyResult("unknown", False, "Missing first or last name.")

        queries = []
        full = f"{first_name} {last_name}".strip()
        for q in (full, last_name, first_name):
            q = _clean_text(q)
            if q and q.lower() not in {x.lower() for x in queries}:
                queries.append(q)

        best_review: Optional[RebnyResult] = None
        for query in queries:
            text = self.search_text(query)
            result = _parse_rebny_text(first_name, last_name, text, query_used=query)
            if result.rebny_match:
                return result
            if result.rebny_status == "review" and (
                best_review is None or result.rebny_confidence > best_review.rebny_confidence
            ):
                best_review = result
            # If full-name search says no results, try last-name search next.
            time.sleep(0.2)

        if best_review is not None:
            return best_review

        return RebnyResult(
            rebny_status="not found",
            rebny_match=False,
            rebny_detail="No matching REBNY directory result returned after full-name and fallback searches.",
            rebny_confidence=0,
            rebny_query_used=", ".join(queries),
        )


_CLIENT: Optional[RebnyDirectoryClient] = None


def _get_client() -> RebnyDirectoryClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = RebnyDirectoryClient(headless=True)
    return _CLIENT


@lru_cache(maxsize=4096)
def _lookup_rebny_cached(first_name: str, last_name: str) -> tuple:
    client = _get_client()
    result = client.lookup(first_name, last_name)
    return tuple(result.to_dict().items())


def lookup_rebny(first_name: str, last_name: str) -> dict:
    first_name = _clean_text(str(first_name or ""))
    last_name = _clean_text(str(last_name or ""))
    if not first_name or not last_name:
        return RebnyResult("unknown", False, "Missing first or last name.").to_dict()

    try:
        return dict(_lookup_rebny_cached(first_name, last_name))
    except Exception as first_error:
        # Restart once. Browser sessions can get stale on Streamlit Cloud.
        global _CLIENT
        try:
            if _CLIENT is not None:
                _CLIENT.close()
        except Exception:
            pass
        _CLIENT = None
        try:
            result = _get_client().lookup(first_name, last_name)
            _lookup_rebny_cached.cache_clear()
            return result.to_dict()
        except Exception as second_error:
            return RebnyResult(
                rebny_status="error",
                rebny_match=False,
                rebny_detail=f"REBNY lookup failed: {second_error or first_error}",
            ).to_dict()


# Compatibility aliases for older app versions.
check_rebny = lookup_rebny
lookup_rebny_member = lookup_rebny


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("first")
    parser.add_argument("last")
    args = parser.parse_args()
    print(lookup_rebny(args.first, args.last))
