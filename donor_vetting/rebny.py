"""REBNY public member-directory lookup.

This module deliberately does *not* treat a directory count as a match. It:
1. fetches the public REBNY Member Directory search page for the person's name,
2. extracts actual member cards / candidate names from the returned HTML,
3. compares the candidates to the searched first+last name with conservative rules,
4. returns FOUND only when the returned member name itself matches.

There is no public REBNY API documented on the site, so this client uses the
public directory page and includes throttling/caching hooks rather than bulk
copying the directory.
"""
from __future__ import annotations

import html
import json
import re
import time
from dataclasses import asdict
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode

from .models import RebnyCandidate, RebnyLookupResult
from .normalization import first_last_match, looks_like_person_line, normalize_name, similarity

REBNY_MEMBERS_URL = "https://www.rebny.com/members/"
DEFAULT_TIMEOUT = 25
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

BOILERPLATE_PATTERNS = [
    r"^contact us", r"^search$", r"^member directory$", r"^filter", r"^sort$",
    r"^login$", r"^join us$", r"^privacy policy$", r"^terms of use$",
    r"^©", r"^image:", r"btn_arrow", r"icn_", r"^residential listing service",
    r"^events", r"^education", r"^advocacy", r"^about", r"^organization",
    r"^membership$", r"^other$", r"^no search results found$",
]

CARD_CLASS_RE = re.compile(r"(member|directory|result|card|listing|person)", re.I)
COUNT_RE = re.compile(r"\b(\d{1,5})\s+Members?\b", re.I)
NO_RESULTS_RE = re.compile(r"No\s+Search\s+Results\s+Found", re.I)
TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_STYLE_RE = re.compile(r"<script\b[^>]*>.*?</script>|<style\b[^>]*>.*?</style>", re.I | re.S)


def build_rebny_search_url(query: str) -> str:
    return f"{REBNY_MEMBERS_URL}?{urlencode({'search': query})}"


def _clean_line(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value).strip(" \t\r\n-|•")
    return value


def _is_boilerplate(line: str) -> bool:
    normalized = _clean_line(line).lower()
    if not normalized:
        return True
    return any(re.search(pattern, normalized) for pattern in BOILERPLATE_PATTERNS)


def _plain_text_lines(html_text: str) -> list[str]:
    cleaned = SCRIPT_STYLE_RE.sub(" ", html_text or "")
    # Preserve common block boundaries before removing tags.
    cleaned = re.sub(r"</(?:div|p|h1|h2|h3|h4|li|article|section|br)>", "\n", cleaned, flags=re.I)
    cleaned = TAG_RE.sub(" ", cleaned)
    lines = [_clean_line(line) for line in cleaned.splitlines()]
    return [line for line in lines if line and not _is_boilerplate(line)]


def _extract_result_count(html_text: str) -> int:
    counts = [int(match.group(1)) for match in COUNT_RE.finditer(html_text or "")]
    return max(counts) if counts else 0


def _candidate_from_lines(lines: list[str], source_url: str) -> list[RebnyCandidate]:
    candidates: list[RebnyCandidate] = []
    for idx, line in enumerate(lines):
        if not looks_like_person_line(line):
            continue
        # Attach the next few non-person lines as company/category/role detail.
        detail_lines: list[str] = []
        for nxt in lines[idx + 1 : idx + 5]:
            if looks_like_person_line(nxt):
                break
            if not _is_boilerplate(nxt):
                detail_lines.append(nxt)
        company = detail_lines[0] if detail_lines else ""
        role = detail_lines[1] if len(detail_lines) > 1 else ""
        category = detail_lines[2] if len(detail_lines) > 2 else ""
        raw = " | ".join([line] + detail_lines)
        candidates.append(
            RebnyCandidate(
                name=line,
                company=company,
                role=role,
                category=category,
                source_url=source_url,
                raw_text=raw,
            )
        )
    return candidates


def _extract_json_candidates(html_text: str, source_url: str) -> list[RebnyCandidate]:
    """Extract candidates from embedded JSON/script blobs when present."""
    out: list[RebnyCandidate] = []
    for script_match in re.finditer(r"<script[^>]*>(.*?)</script>", html_text or "", flags=re.I | re.S):
        blob = html.unescape(script_match.group(1) or "")
        if "name" not in blob.lower():
            continue
        # First try full JSON script blocks.
        try:
            parsed = json.loads(blob.strip())
        except Exception:
            parsed = None
        objects: list[Any] = []
        if parsed is not None:
            objects.append(parsed)
        else:
            # Fall back to small object fragments with name/company-like keys.
            for obj_text in re.findall(r"\{[^{}]{0,2000}?\}", blob, flags=re.S):
                if re.search(r'"(?:name|title|firstName|lastName)"\s*:', obj_text):
                    try:
                        objects.append(json.loads(obj_text))
                    except Exception:
                        continue

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                keys = {str(k).lower(): k for k in value}
                name = ""
                if "name" in keys:
                    name = str(value[keys["name"]] or "")
                elif "title" in keys:
                    name = str(value[keys["title"]] or "")
                elif "firstname" in keys and "lastname" in keys:
                    name = f"{value[keys['firstname']]} {value[keys['lastname']]}"
                if looks_like_person_line(name):
                    company = ""
                    for key in ("company", "organization", "firm", "memberCompany"):
                        if key.lower() in keys:
                            company = str(value[keys[key.lower()]] or "")
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
    """Optional BeautifulSoup extraction; falls back gracefully if bs4 is absent."""
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
        class_text = " ".join(tag.get("class", []) if isinstance(tag.get("class"), list) else [str(tag.get("class", ""))])
        data_text = " ".join(f"{k}={v}" for k, v in tag.attrs.items() if k.startswith("data-"))
        if CARD_CLASS_RE.search(class_text + " " + data_text):
            text = _clean_line(tag.get_text("\n"))
            if 8 <= len(text) <= 800:
                blocks.append(tag)

    # De-duplicate nested blocks by their text.
    seen_text: set[str] = set()
    unique_blocks = []
    for block in blocks:
        text = _clean_line(block.get_text(" "))
        if text not in seen_text:
            seen_text.add(text)
            unique_blocks.append(block)

    for block in unique_blocks:
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


def extract_member_candidates(html_text: str, source_url: str = REBNY_MEMBERS_URL) -> list[RebnyCandidate]:
    """Parse possible REBNY member entries from returned public-directory HTML."""
    candidates: list[RebnyCandidate] = []
    candidates.extend(_extract_json_candidates(html_text, source_url))
    candidates.extend(_extract_card_candidates_with_bs4(html_text, source_url))
    candidates.extend(_candidate_from_lines(_plain_text_lines(html_text), source_url))

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
        # Middle names, initials, and suffixes should not hurt an otherwise exact first+last match.
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
            detail="No candidate member cards were returned by the public REBNY directory.",
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


@lru_cache(maxsize=2048)
def _fetch_rebny_html_cached(query: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str, str]:
    import requests  # imported lazily so unit tests can run with stdlib only

    url = build_rebny_search_url(query)
    response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    return response.status_code, response.text, response.url


def fetch_rebny_html(query: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str, str]:
    return _fetch_rebny_html_cached(query, timeout)


def _fetch_with_playwright(query: str, timeout_ms: int = 25_000) -> tuple[int, str, str] | None:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return None

    url = build_rebny_search_url(query)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent=DEFAULT_HEADERS["User-Agent"])
        page.goto(url, wait_until="networkidle", timeout=timeout_ms)
        try:
            page.wait_for_selector("text=/Members|No Search Results Found/", timeout=8_000)
        except Exception:
            pass
        content = page.content()
        final_url = page.url
        browser.close()
    return 200, content, final_url


def lookup_rebny(
    first_name: str,
    last_name: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    use_playwright_fallback: bool = True,
    polite_delay_seconds: float = 0.0,
) -> RebnyLookupResult:
    """Look up one person in the public REBNY Member Directory."""
    first = (first_name or "").strip()
    last = (last_name or "").strip()
    if not first or not last:
        return RebnyLookupResult(
            status="invalid input",
            match=False,
            needs_review=True,
            confidence=0,
            detail="Both first and last name are required for REBNY matching.",
            source_url=REBNY_MEMBERS_URL,
        )

    query = f"{first} {last}".strip()
    if polite_delay_seconds:
        time.sleep(polite_delay_seconds)

    try:
        status_code, html_text, final_url = fetch_rebny_html(query, timeout=timeout)
    except Exception as exc:
        return RebnyLookupResult(
            status="error",
            match=False,
            needs_review=True,
            confidence=0,
            detail=f"Could not reach REBNY public directory: {exc}",
            source_url=build_rebny_search_url(query),
        )

    candidates = extract_member_candidates(html_text, final_url)
    result_count = _extract_result_count(html_text)
    no_results = bool(NO_RESULTS_RE.search(html_text or ""))

    # If the HTTP body is just the shell and Playwright is available, render it.
    if use_playwright_fallback and status_code == 200 and not candidates and not no_results:
        rendered = _fetch_with_playwright(query)
        if rendered:
            status_code, html_text, final_url = rendered
            candidates = extract_member_candidates(html_text, final_url)
            result_count = _extract_result_count(html_text)
            no_results = bool(NO_RESULTS_RE.search(html_text or ""))

    if status_code >= 400:
        return RebnyLookupResult(
            status="error",
            match=False,
            needs_review=True,
            confidence=0,
            detail=f"REBNY returned HTTP {status_code}.",
            source_url=final_url,
        )

    if no_results and not candidates:
        return RebnyLookupResult(
            status="not found",
            match=False,
            needs_review=False,
            confidence=0,
            result_count=result_count,
            detail="REBNY public directory returned 'No Search Results Found'.",
            source_url=final_url,
        )

    result, _ = classify_rebny_match(first, last, candidates)
    result.result_count = max(result.result_count, result_count, len(candidates))
    if not result.source_url:
        result.source_url = final_url
    return result


def result_to_dict(result: RebnyLookupResult) -> dict[str, Any]:
    """JSON-safe serialization for logging/debug exports."""
    data = asdict(result)
    data["candidates"] = [asdict(c) for c in result.candidates]
    return data
