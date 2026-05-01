"""REBNY public member-directory lookup.

This file is intentionally quiet and app-compatible: lookup_rebny() returns a
RebnyLookupResult object with .as_row(), because app.py expects that shape.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import time
from dataclasses import asdict
from functools import lru_cache
from typing import Any, Optional
from urllib.parse import urlencode

from .models import RebnyCandidate, RebnyLookupResult
from .normalization import first_last_match, looks_like_person_line, normalize_name, similarity

REBNY_MEMBERS_URL = "https://www.rebny.com/members/"
DEFAULT_TIMEOUT = 25
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

BOILERPLATE_PATTERNS = [
    r"^contact us", r"^search$", r"^member directory$", r"^filter", r"^sort$",
    r"^login$", r"^join us$", r"^privacy policy$", r"^terms of use$",
    r"^©", r"^image:", r"btn_arrow", r"icn_", r"^residential listing service",
    r"^events", r"^education", r"^advocacy", r"^about", r"^organization",
    r"^membership$", r"^other$", r"^no search results found$", r"^stay connected$",
    r"^access member resources$", r"^become a member$", r"^webinar hub$",
]
CARD_CLASS_RE = re.compile(r"(member|directory|result|card|listing|person)", re.I)
COUNT_RE = re.compile(r"\b(\d{1,6})\s+Members?\b", re.I)
NO_RESULTS_RE = re.compile(r"No\s+Search\s+Results\s+Found", re.I)
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>|<noscript\b[^>]*>.*?</noscript>", re.I | re.S)
COMPANY_WORDS = {
    "llc", "inc", "corp", "corporation", "company", "co", "ltd", "lp", "llp",
    "pllc", "pc", "management", "services", "service", "realty", "estate",
    "brokerage", "commercial", "residential", "owner", "owners", "group",
    "properties", "property", "partners", "associates", "association",
    "development", "capital", "holdings", "advisors", "advisory", "agency",
}

try:  # optional dependency; Streamlit Cloud needs playwright installed + chromium available
    from playwright.sync_api import sync_playwright
except Exception:  # pragma: no cover
    sync_playwright = None


def build_rebny_search_url(query: str) -> str:
    return f"{REBNY_MEMBERS_URL}?{urlencode({'search': query})}"


def _clean_line(value: str) -> str:
    value = html.unescape(value or "")
    value = value.replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n-|•")
    return value


def _is_boilerplate(line: str) -> bool:
    normalized = _clean_line(line).lower()
    if not normalized:
        return True
    return any(re.search(pattern, normalized) for pattern in BOILERPLATE_PATTERNS)


def _plain_text_lines(text: str) -> list[str]:
    cleaned = SCRIPT_STYLE_RE.sub(" ", text or "")
    cleaned = re.sub(r"</(?:div|p|h1|h2|h3|h4|li|article|section|br|span|a)>", "\n", cleaned, flags=re.I)
    cleaned = TAG_RE.sub(" ", cleaned)
    lines = [_clean_line(line) for line in cleaned.splitlines()]
    return [line for line in lines if line and not _is_boilerplate(line)]


def _extract_result_count(text: str) -> int:
    counts = []
    for match in COUNT_RE.finditer(text or ""):
        try:
            counts.append(int(match.group(1)))
        except ValueError:
            pass
    return max(counts) if counts else 0


def _line_is_probably_company(line: str) -> bool:
    tokens = set(normalize_name(line).split())
    return bool(tokens & COMPANY_WORDS)


def _candidate_from_lines(lines: list[str], source_url: str) -> list[RebnyCandidate]:
    candidates: list[RebnyCandidate] = []
    for idx, line in enumerate(lines):
        if not looks_like_person_line(line):
            continue
        if _line_is_probably_company(line):
            continue
        detail_lines: list[str] = []
        for nxt in lines[idx + 1 : idx + 5]:
            if looks_like_person_line(nxt):
                break
            if not _is_boilerplate(nxt):
                detail_lines.append(nxt)
        candidates.append(
            RebnyCandidate(
                name=line,
                company=detail_lines[0] if detail_lines else "",
                role=detail_lines[1] if len(detail_lines) > 1 else "",
                category=detail_lines[2] if len(detail_lines) > 2 else "",
                source_url=source_url,
                raw_text=" | ".join([line] + detail_lines),
            )
        )
    return candidates


def _extract_json_candidates(html_text: str, source_url: str) -> list[RebnyCandidate]:
    out: list[RebnyCandidate] = []
    for script_match in re.finditer(r"<script[^>]*>(.*?)</script>", html_text or "", flags=re.I | re.S):
        blob = html.unescape(script_match.group(1) or "")
        if "name" not in blob.lower() and "member" not in blob.lower():
            continue
        objects: list[Any] = []
        try:
            parsed = json.loads(blob.strip())
            objects.append(parsed)
        except Exception:
            for obj_text in re.findall(r"\{[^{}]{0,2500}?\}", blob, flags=re.S):
                if re.search(r'"(?:name|title|firstName|lastName|memberName)"\s*:', obj_text):
                    try:
                        objects.append(json.loads(obj_text))
                    except Exception:
                        continue

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                keys = {str(k).lower(): k for k in value}
                name = ""
                for key in ("name", "title", "membername", "displayname"):
                    if key in keys:
                        name = str(value[keys[key]] or "")
                        break
                if not name and "firstname" in keys and "lastname" in keys:
                    name = f"{value[keys['firstname']]} {value[keys['lastname']]}"
                if looks_like_person_line(name):
                    company = ""
                    for key in ("company", "organization", "firm", "membercompany", "office"):
                        if key in keys:
                            company = str(value[keys[key]] or "")
                            break
                    out.append(RebnyCandidate(name=_clean_line(name), company=_clean_line(company), source_url=source_url))
                for nested in value.values():
                    walk(nested)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        for obj in objects:
            walk(obj)
    return out


def _extract_card_candidates_with_bs4(html_text: str, source_url: str) -> list[RebnyCandidate]:
    try:
        from bs4 import BeautifulSoup  # type: ignore
    except Exception:
        return []

    soup = BeautifulSoup(html_text or "", "html.parser")
    for bad in soup(["script", "style", "noscript"]):
        bad.decompose()

    candidates: list[RebnyCandidate] = []
    blocks = []
    for tag in soup.find_all(True):
        class_attr = tag.get("class", [])
        class_text = " ".join(class_attr if isinstance(class_attr, list) else [str(class_attr)])
        data_text = " ".join(f"{k}={v}" for k, v in tag.attrs.items() if str(k).startswith("data-"))
        if CARD_CLASS_RE.search(class_text + " " + data_text):
            text = _clean_line(tag.get_text("\n"))
            if 8 <= len(text) <= 900:
                blocks.append(tag)

    seen_text: set[str] = set()
    for block in blocks:
        block_text = _clean_line(block.get_text(" "))
        if not block_text or block_text in seen_text:
            continue
        seen_text.add(block_text)
        heading_text = ""
        for selector in ["h1", "h2", "h3", "h4", "a", "strong"]:
            found = block.find(selector)
            if found:
                candidate = _clean_line(found.get_text(" "))
                if looks_like_person_line(candidate):
                    heading_text = candidate
                    break
        lines = [_clean_line(x) for x in block.get_text("\n").splitlines()]
        lines = [line for line in lines if line and not _is_boilerplate(line)]
        if not heading_text:
            heading_text = next((line for line in lines if looks_like_person_line(line)), "")
        if not heading_text:
            continue
        detail_lines = [line for line in lines if line != heading_text]
        candidates.append(
            RebnyCandidate(
                name=heading_text,
                company=detail_lines[0] if detail_lines else "",
                role=detail_lines[1] if len(detail_lines) > 1 else "",
                category=detail_lines[2] if len(detail_lines) > 2 else "",
                source_url=source_url,
                raw_text=" | ".join(lines),
            )
        )
    return candidates


def extract_member_candidates(text: str, source_url: str = REBNY_MEMBERS_URL) -> list[RebnyCandidate]:
    candidates: list[RebnyCandidate] = []
    candidates.extend(_extract_json_candidates(text, source_url))
    candidates.extend(_extract_card_candidates_with_bs4(text, source_url))
    candidates.extend(_candidate_from_lines(_plain_text_lines(text), source_url))

    seen: set[tuple[str, str]] = set()
    unique: list[RebnyCandidate] = []
    for candidate in candidates:
        key = (normalize_name(candidate.name), normalize_name(candidate.company))
        if not key[0] or key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def score_rebny_candidate(first_name: str, last_name: str, candidate: RebnyCandidate) -> tuple[int, str]:
    full_query = f"{first_name} {last_name}".strip()
    score = int(round(similarity(full_query, candidate.name)))
    exact, reason = first_last_match(first_name, last_name, candidate.name)
    if exact:
        score = max(score, 98)
    elif "first initial" in reason:
        score = max(score, 88)
    return min(score, 100), reason


def classify_rebny_match(first_name: str, last_name: str, candidates: list[RebnyCandidate]) -> tuple[RebnyLookupResult, RebnyCandidate | None]:
    if not candidates:
        return RebnyLookupResult(
            status="not found",
            match=False,
            needs_review=False,
            confidence=0,
            result_count=0,
            detail="No matching member name was returned by the public REBNY directory.",
            source_url=REBNY_MEMBERS_URL,
        ), None

    scored: list[tuple[int, str, RebnyCandidate]] = []
    for candidate in candidates:
        score, reason = score_rebny_candidate(first_name, last_name, candidate)
        scored.append((score, reason, candidate))
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_reason, best = scored[0]
    exact, _ = first_last_match(first_name, last_name, best.name)

    if exact and best_score >= 96:
        return RebnyLookupResult(
            status="FOUND",
            match=True,
            needs_review=False,
            confidence=best_score,
            matched_name=best.name,
            company=best.company,
            category=best.category,
            role=best.role,
            result_count=len(candidates),
            detail=f"Matched returned REBNY member name by first+last tokens ({best_score}/100).",
            source_url=best.source_url,
            candidates=candidates[:10],
        ), best

    if best_score >= 86:
        return RebnyLookupResult(
            status="review",
            match=False,
            needs_review=True,
            confidence=best_score,
            matched_name=best.name,
            company=best.company,
            category=best.category,
            role=best.role,
            result_count=len(candidates),
            detail=f"Possible REBNY match, but not strong enough to auto-mark FOUND: {best_reason} ({best_score}/100).",
            source_url=best.source_url,
            candidates=candidates[:10],
        ), best

    return RebnyLookupResult(
        status="not found",
        match=False,
        needs_review=False,
        confidence=best_score,
        matched_name=best.name,
        company=best.company,
        category=best.category,
        role=best.role,
        result_count=len(candidates),
        detail=f"Directory returned candidate(s), but none matched the searched first+last name. Best was {best.name} ({best_score}/100).",
        source_url=best.source_url,
        candidates=candidates[:10],
    ), best


def _invalid_or_error(status: str, detail: str, source_url: str = REBNY_MEMBERS_URL, review: bool = True) -> RebnyLookupResult:
    return RebnyLookupResult(
        status=status,
        match=False,
        needs_review=review,
        confidence=0,
        detail=detail,
        source_url=source_url,
    )


def _ensure_chromium_installed() -> None:
    if sync_playwright is None:
        return
    try:
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
            user_agent=DEFAULT_HEADERS["User-Agent"],
        )
        self._page = context.new_page()
        self._page.goto(REBNY_MEMBERS_URL, wait_until="domcontentloaded", timeout=45_000)
        try:
            self._page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        self._page.wait_for_timeout(1_000)

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
            try:
                loc = page.locator(selector)
                for i in range(min(loc.count(), 8)):
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
        for selector in [
            "xpath=(//*[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'search by name')]/following::button[1])",
            "button[type='submit']",
            "input[type='submit']",
            "button[aria-label*='Search' i]",
        ]:
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
            inp.click(timeout=5_000)
            inp.press("Control+A")
            inp.fill(query, timeout=5_000)
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

    def search_text(self, query: str) -> tuple[str, str]:
        self._start()
        if not self._set_query(query):
            raise RuntimeError("Could not find the REBNY Search By Name input")
        for _ in range(8):
            self._page.wait_for_timeout(500)
            try:
                self._page.wait_for_load_state("networkidle", timeout=1_000)
            except Exception:
                pass
        return self._page.locator("body").inner_text(timeout=10_000), self._page.url

    def lookup(self, first_name: str, last_name: str) -> RebnyLookupResult:
        first_name = _clean_line(str(first_name or ""))
        last_name = _clean_line(str(last_name or ""))
        if not first_name or not last_name:
            return _invalid_or_error("invalid input", "Both first and last name are required for REBNY matching.", review=True)

        queries: list[str] = []
        for q in (f"{first_name} {last_name}".strip(), last_name, first_name):
            q = _clean_line(q)
            if q and q.lower() not in {x.lower() for x in queries}:
                queries.append(q)

        best_review: Optional[RebnyLookupResult] = None
        last_not_found: Optional[RebnyLookupResult] = None
        for query in queries:
            text, final_url = self.search_text(query)
            candidates = extract_member_candidates(text, final_url)
            result, _ = classify_rebny_match(first_name, last_name, candidates)
            result.result_count = max(result.result_count, _extract_result_count(text), len(candidates))
            result.source_url = final_url or REBNY_MEMBERS_URL
            if query != queries[0]:
                result.detail = f"Query used: {query}. {result.detail}"
            if result.match:
                return result
            if result.needs_review and (best_review is None or result.confidence > best_review.confidence):
                best_review = result
            if result.status == "not found":
                last_not_found = result
            time.sleep(0.2)

        if best_review is not None:
            return best_review
        if last_not_found is not None:
            last_not_found.detail = "No matching REBNY directory result returned after full-name and fallback searches."
            return last_not_found
        return RebnyLookupResult(
            status="not found",
            match=False,
            needs_review=False,
            confidence=0,
            result_count=0,
            detail="No matching REBNY directory result returned.",
            source_url=REBNY_MEMBERS_URL,
        )


_CLIENT: Optional[RebnyDirectoryClient] = None


def _get_client() -> RebnyDirectoryClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = RebnyDirectoryClient(headless=True)
    return _CLIENT


@lru_cache(maxsize=4096)
def _lookup_rebny_cached(first_name: str, last_name: str) -> RebnyLookupResult:
    return _get_client().lookup(first_name, last_name)


def lookup_rebny(
    first_name: str,
    last_name: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    use_playwright_fallback: bool = True,
    polite_delay_seconds: float = 0.0,
    **_: object,
) -> RebnyLookupResult:
    first = _clean_line(str(first_name or ""))
    last = _clean_line(str(last_name or ""))
    if polite_delay_seconds:
        try:
            time.sleep(float(polite_delay_seconds))
        except Exception:
            pass
    if not first or not last:
        return _invalid_or_error("invalid input", "Both first and last name are required for REBNY matching.", review=True)

    if sync_playwright is None:
        return _invalid_or_error(
            "error",
            "Playwright is required for REBNY's interactive directory search. Add playwright to requirements.txt and chromium to the deployment.",
            source_url=REBNY_MEMBERS_URL,
            review=True,
        )

    try:
        return _lookup_rebny_cached(first, last)
    except Exception as first_error:
        global _CLIENT
        try:
            if _CLIENT is not None:
                _CLIENT.close()
        except Exception:
            pass
        _CLIENT = None
        _lookup_rebny_cached.cache_clear()
        try:
            return _get_client().lookup(first, last)
        except Exception as second_error:
            return _invalid_or_error(
                "error",
                f"REBNY lookup failed: {second_error or first_error}",
                source_url=REBNY_MEMBERS_URL,
                review=True,
            )


def result_to_dict(result: RebnyLookupResult) -> dict[str, Any]:
    data = asdict(result)
    data["candidates"] = [asdict(c) for c in result.candidates]
    return data


check_rebny = lookup_rebny
lookup_rebny_member = lookup_rebny


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("first")
    parser.add_argument("last")
    args = parser.parse_args()
    print(result_to_dict(lookup_rebny(args.first, args.last)))
