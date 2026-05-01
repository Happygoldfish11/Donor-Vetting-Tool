from __future__ import annotations

import argparse
import json
import re
import string
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vetting_core import RebnyMember, dedupe_members, normalize_text  # noqa: E402

REBNY_MEMBERS_URL = "https://www.rebny.com/members/"

SEARCH_INPUT_SELECTORS = [
    "input[placeholder*='Name' i]",
    "input[aria-label*='Name' i]",
    "input[name*='name' i]",
    "input[name*='search' i]",
    "input[type='search']",
    "input[type='text']",
]

SUBMIT_SELECTORS = [
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Search')",
    "a:has-text('Search')",
]

BAD_NAME_LINES = {
    "member directory",
    "search by name",
    "no search results found",
    "members",
    "member resources",
    "stay connected",
    "access member resources",
    "events education",
    "news media",
    "resources",
    "about",
    "organization",
    "leadership",
    "login",
    "join us",
    "contact us",
    "terms of use",
    "privacy policy",
}


def seeds(deep: bool, extra: list[str]) -> list[str]:
    values = [value.strip() for value in extra if value.strip()]
    if deep:
        values.extend(string.ascii_lowercase)
        values.extend(a + b for a in string.ascii_lowercase for b in string.ascii_lowercase)
    elif not values:
        values.extend(string.ascii_lowercase)
    seen = set()
    out = []
    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            out.append(value)
    return out


def looks_like_person_name(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", text).strip()
    norm = normalize_text(cleaned)
    if not cleaned or norm in BAD_NAME_LINES:
        return False
    if len(cleaned) > 80 or len(cleaned) < 4:
        return False
    if "@" in cleaned or "http" in cleaned.lower():
        return False
    if any(word in norm.split() for word in ["button", "arrow", "image", "logo", "copyright"]):
        return False
    tokens = norm.split()
    if len(tokens) < 2 or len(tokens) > 5:
        return False
    if not all(token.isalpha() for token in tokens):
        return False
    return True


def member_from_text(text: str, source_query: str = "", url: str = "") -> RebnyMember | None:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    name = ""
    for line in lines:
        if looks_like_person_name(line):
            name = line
            break
    if not name:
        return None
    company = ""
    title = ""
    for line in lines:
        if line == name:
            continue
        low = normalize_text(line)
        if not company and len(line) <= 90 and not any(x in low for x in ["search", "member directory"]):
            company = line
        elif not title and len(line) <= 90:
            title = line
    return RebnyMember(name=name, company=company, title=title, profile_url=url, raw_text=" | ".join(lines + [f"query={source_query}"]))


def extract_from_json(obj: Any) -> list[RebnyMember]:
    members: list[RebnyMember] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            keys = {str(k).lower(): k for k in value.keys()}
            name = ""
            for key in ["name", "title", "full_name", "fullname", "member_name", "display_name"]:
                if key in keys:
                    raw = value.get(keys[key])
                    if isinstance(raw, str) and looks_like_person_name(raw):
                        name = raw.strip()
                        break
                    if isinstance(raw, dict) and isinstance(raw.get("rendered"), str):
                        stripped = re.sub(r"<[^>]+>", " ", raw["rendered"])
                        if looks_like_person_name(stripped):
                            name = re.sub(r"\s+", " ", stripped).strip()
                            break
            if name:
                company = ""
                for key in ["company", "firm", "organization", "brokerage"]:
                    if key in keys and isinstance(value.get(keys[key]), str):
                        company = value.get(keys[key]).strip()
                        break
                url = ""
                for key in ["url", "link", "permalink"]:
                    if key in keys and isinstance(value.get(keys[key]), str):
                        url = value.get(keys[key]).strip()
                        break
                members.append(RebnyMember(name=name, company=company, profile_url=url, raw_text=json.dumps(value, default=str)[:1000]))
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(obj)
    return members


def install_hint() -> str:
    return "Install browser support with: python -m playwright install chromium"


def find_search_input(page):
    for selector in SEARCH_INPUT_SELECTORS:
        locator = page.locator(selector).first
        try:
            if locator.count() and locator.is_visible(timeout=1500):
                return locator
        except Exception:
            continue
    return None


def submit_search(page) -> None:
    pressed = False
    try:
        page.keyboard.press("Enter")
        pressed = True
    except Exception:
        pass
    for selector in SUBMIT_SELECTORS:
        try:
            button = page.locator(selector).first
            if button.count() and button.is_visible(timeout=1000):
                button.click(timeout=2000)
                return
        except Exception:
            continue
    if not pressed:
        page.wait_for_timeout(300)


def extract_visible_members(page, source_query: str) -> list[RebnyMember]:
    members: list[RebnyMember] = []
    selectors = [
        "article",
        "li",
        "div[class*='member' i]",
        "div[class*='card' i]",
        "div[class*='directory' i]",
        "a[href*='member' i]",
        "h2, h3, h4",
    ]

    for selector in selectors:
        try:
            elements = page.locator(selector)
            count = min(elements.count(), 500)
            for index in range(count):
                element = elements.nth(index)
                try:
                    if not element.is_visible(timeout=300):
                        continue
                    text = element.inner_text(timeout=1000)
                    href = ""
                    try:
                        href = element.get_attribute("href") or ""
                    except Exception:
                        pass
                    member = member_from_text(text, source_query=source_query, url=href)
                    if member:
                        members.append(member)
                except Exception:
                    continue
        except Exception:
            continue

    return dedupe_members(members)


def run_query(page, query: str, wait_ms: int) -> list[RebnyMember]:
    captured_json: list[Any] = []

    def on_response(response):
        try:
            ctype = response.headers.get("content-type", "")
            url = response.url.lower()
            if "json" in ctype or "ajax" in url or "wp-json" in url or "graphql" in url:
                captured_json.append(response.json())
        except Exception:
            return

    page.on("response", on_response)

    search_input = find_search_input(page)
    if search_input is not None:
        search_input.fill(query, timeout=5000)
        submit_search(page)
    else:
        page.goto(f"{REBNY_MEMBERS_URL}?search={query}", wait_until="networkidle", timeout=30000)

    page.wait_for_timeout(wait_ms)

    members: list[RebnyMember] = []
    for payload in captured_json:
        members.extend(extract_from_json(payload))
    members.extend(extract_visible_members(page, source_query=query))

    try:
        page.remove_listener("response", on_response)
    except Exception:
        pass

    return dedupe_members(members)


def scrape_rebny_members(headful: bool, deep: bool, extra_queries: list[str], wait_ms: int) -> list[RebnyMember]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("Missing playwright. Run: pip install playwright\n" + install_hint()) from exc

    all_members: list[RebnyMember] = []
    query_list = seeds(deep=deep, extra=extra_queries)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headful)
        page = browser.new_page(viewport={"width": 1440, "height": 1200})
        page.goto(REBNY_MEMBERS_URL, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(1200)

        for index, query in enumerate(query_list, start=1):
            print(f"[{index}/{len(query_list)}] searching {query!r}")
            try:
                members = run_query(page, query=query, wait_ms=wait_ms)
                all_members.extend(members)
                print(f"  found {len(members)} visible/API candidate(s); total unique {len(dedupe_members(all_members))}")
            except Exception as exc:
                print(f"  warning: query {query!r} failed: {exc}")
            time.sleep(0.2)

        browser.close()

    return dedupe_members(all_members)


def save_members(members: list[RebnyMember], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "name": member.display_name,
            "company": member.company,
            "title": member.title,
            "profile_url": member.profile_url,
            "raw_text": member.raw_text,
        }
        for member in members
    ]
    df = pd.DataFrame(rows).sort_values("name") if rows else pd.DataFrame(columns=["name", "company", "title", "profile_url", "raw_text"])
    df.to_excel(output, index=False)
    print(f"Wrote {len(df)} unique member row(s) to {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a local REBNY member cache for the donor vetting app.")
    parser.add_argument("--output", default="data/rebny_members.xlsx", help="Output XLSX path.")
    parser.add_argument("--deep", action="store_true", help="Search A-Z and AA-ZZ. Slower but more complete.")
    parser.add_argument("--headful", action="store_true", help="Show the browser while scraping.")
    parser.add_argument("--wait-ms", type=int, default=1800, help="Wait after each search.")
    parser.add_argument("--query", action="append", default=[], help="Extra search query. Can be used multiple times.")
    args = parser.parse_args()

    members = scrape_rebny_members(
        headful=args.headful,
        deep=args.deep,
        extra_queries=args.query,
        wait_ms=args.wait_ms,
    )
    save_members(members, Path(args.output))


if __name__ == "__main__":
    main()
