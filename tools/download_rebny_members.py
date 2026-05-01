from __future__ import annotations

import argparse
import json
import re
import string
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

NOISE = {
    "contact us", "search", "login", "join us", "member resources", "stay connected",
    "residential listing service", "webinar hub", "nyc lease", "events & education",
    "upcoming events", "sponsorships", "news & media", "resources", "press releases",
    "photos", "videos", "podcast", "style guide", "advocacy", "research & reports",
    "testimony & comments", "about", "organization", "staff", "chair", "terms of use",
    "privacy policy", "membership", "other", "careers", "faq", "member disputes",
    "member directory", "filter & sort", "search by name", "no search results found",
    "btn_arrow_white", "icn_accdn_open_white", "image: rebny", "image: nyc skyline",
}

@dataclass(frozen=True)
class MemberRecord:
    name: str
    company: str = ""
    category: str = ""
    source_query: str = ""
    source_url: str = "https://www.rebny.com/members/"
    raw_text: str = ""

    def key(self) -> tuple[str, str, str]:
        return (norm(self.name), norm(self.company), norm(self.category))


def norm(s: str) -> str:
    s = re.sub(r"\s+", " ", str(s or "").replace("\xa0", " ")).strip().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").replace("\xa0", " ")).strip()


def is_noise_line(line: str) -> bool:
    n = norm(line)
    if not n:
        return True
    if n in NOISE:
        return True
    if len(n) <= 1:
        return True
    if n.endswith(" members") or n == "members":
        return True
    if re.fullmatch(r"page\s+\d+", n):
        return True
    if "copyright" in n or n.startswith("©"):
        return True
    if "rebny 2022 logo" in n:
        return True
    return False


def likely_person_or_org_name(line: str) -> bool:
    line = clean(line)
    if is_noise_line(line):
        return False
    n = norm(line)
    if len(n) < 3 or len(n) > 90:
        return False
    if any(x in n for x in ["http", "@", "click", "learn more", "access member"]):
        return False
    tokens = n.split()
    if len(tokens) > 8:
        return False
    if re.search(r"\d{3}[-.) ]?\d{3}[- ]?\d{4}", line):
        return False
    if re.search(r"\b[A-Za-z]\.?\s+[A-Za-z]", line):
        return True
    legal_org = {"llc", "inc", "corp", "company", "properties", "realty", "group", "estate", "management", "partners"}
    if any(tok in legal_org for tok in tokens):
        return True
    return len(tokens) >= 2 and line[0].isupper()


def parse_text_block(text: str, source_query: str = "") -> list[MemberRecord]:
    raw_lines = [clean(x) for x in re.split(r"[\n\r]+", text)]
    lines = [x for x in raw_lines if x and not is_noise_line(x)]
    records: list[MemberRecord] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not likely_person_or_org_name(line):
            i += 1
            continue
        name = line.strip(" -–—")
        company = ""
        category = ""
        raw = [line]
        if i + 1 < len(lines):
            nxt = lines[i + 1]
            if not likely_person_or_org_name(nxt) or " - " in nxt or "Residential" in nxt or "Commercial" in nxt:
                raw.append(nxt)
                parts = [clean(p) for p in re.split(r"\s+-\s+|\s+–\s+|\s+—\s+", nxt) if clean(p)]
                if parts:
                    company = parts[0]
                    category = " - ".join(parts[1:])
                else:
                    company = nxt
                i += 1
        if name and not is_noise_line(name):
            records.append(MemberRecord(name=name, company=company, category=category, source_query=source_query, raw_text=" | ".join(raw)))
        i += 1
    return records


def extract_records_from_json(obj: Any, source_query: str = "") -> list[MemberRecord]:
    records: list[MemberRecord] = []
    def walk(x: Any):
        if isinstance(x, dict):
            lowered = {str(k).lower(): v for k, v in x.items()}
            name = first_value(lowered, ["name", "title", "member_name", "fullname", "full_name", "display_name"])
            if isinstance(name, dict):
                name = first_value({str(k).lower(): v for k, v in name.items()}, ["rendered", "value", "text"])
            if isinstance(name, str) and likely_person_or_org_name(strip_html(name)):
                company = first_value(lowered, ["company", "organization", "firm", "brokerage", "employer"])
                category = first_value(lowered, ["category", "member_type", "membership_type", "division", "type"])
                records.append(MemberRecord(
                    name=strip_html(name),
                    company=strip_html(company) if isinstance(company, str) else "",
                    category=strip_html(category) if isinstance(category, str) else "",
                    source_query=source_query,
                    raw_text=json.dumps(x, ensure_ascii=False)[:1500],
                ))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for item in x:
                walk(item)
    walk(obj)
    return records


def first_value(d: dict, keys: list[str]):
    for key in keys:
        if key in d and d[key]:
            return d[key]
    for key, value in d.items():
        if any(k in key for k in keys) and value:
            return value
    return ""


def strip_html(s: str) -> str:
    if not isinstance(s, str):
        return ""
    return clean(BeautifulSoup(s, "lxml").get_text(" "))


def unique(records: Iterable[MemberRecord]) -> list[MemberRecord]:
    seen = set()
    out = []
    for r in records:
        if not r.name or is_noise_line(r.name):
            continue
        key = r.key()
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return sorted(out, key=lambda r: (norm(r.name), norm(r.company)))


def find_search_input(page):
    selectors = [
        "input[type='search']",
        "input[placeholder*='Search' i]",
        "input[aria-label*='Search' i]",
        "input[name*='search' i]",
        "input[type='text']",
    ]
    for selector in selectors:
        loc = page.locator(selector)
        count = loc.count()
        for i in range(count):
            item = loc.nth(i)
            try:
                if item.is_visible() and item.is_enabled():
                    return item
            except Exception:
                pass
    return None


def click_submit(page):
    candidates = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Search')",
        "a:has-text('Search')",
        "button[class*='search' i]",
    ]
    for selector in candidates:
        loc = page.locator(selector)
        for i in range(min(loc.count(), 5)):
            try:
                if loc.nth(i).is_visible() and loc.nth(i).is_enabled():
                    loc.nth(i).click(timeout=1000)
                    return True
            except Exception:
                pass
    return False


def settle(page, wait_ms: int = 1200):
    try:
        page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:
        pass
    page.wait_for_timeout(wait_ms)
    for _ in range(3):
        try:
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(350)
        except Exception:
            break
    for txt in ["Load More", "Show More", "More"]:
        try:
            btn = page.get_by_text(txt, exact=False)
            for i in range(min(btn.count(), 3)):
                if btn.nth(i).is_visible():
                    btn.nth(i).click(timeout=1000)
                    page.wait_for_timeout(900)
        except Exception:
            pass


def extract_dom_blocks(page, source_query: str) -> list[MemberRecord]:
    selectors = [
        "[class*='member' i]", "[class*='directory' i]", "[class*='result' i]",
        "article", "li", ".card", "main div",
    ]
    records: list[MemberRecord] = []
    for selector in selectors:
        try:
            texts = page.locator(selector).evaluate_all("""
                els => els.map(e => e.innerText || '').filter(t => t && t.trim().length > 3)
            """)
        except Exception:
            continue
        for t in texts:
            records.extend(parse_text_block(t, source_query))
    try:
        records.extend(parse_text_block(page.locator("body").inner_text(timeout=5000), source_query))
    except Exception:
        pass
    return unique(records)


def search_once(page, query: str, wait_ms: int) -> list[MemberRecord]:
    inp = find_search_input(page)
    if inp is None:
        raise RuntimeError("Could not find the Search By Name input on the REBNY page.")
    try:
        inp.fill("")
        inp.fill(query)
        page.wait_for_timeout(250)
        inp.press("Enter")
    except Exception:
        inp.fill(query)
        click_submit(page)
    settle(page, wait_ms)
    return extract_dom_blocks(page, query)


def count_hint(page) -> int | None:
    try:
        text = page.locator("body").inner_text(timeout=3000)
    except Exception:
        return None
    m = re.search(r"\b(\d{1,6})\s+Members\b", text, re.I)
    return int(m.group(1)) if m else None


def make_prefixes(deep: bool, max_prefix_len: int) -> list[str]:
    letters = string.ascii_lowercase
    prefixes = list(letters)
    if deep and max_prefix_len >= 2:
        prefixes += [a + b for a in letters for b in letters]
    if deep and max_prefix_len >= 3:
        common = "aeiourstlnmcpdbghfwykvjxzq"
        prefixes += [a + b + c for a in letters for b in letters for c in common]
    return prefixes


def setup_network_capture(page, diagnostics_dir: Path, network_records: list[MemberRecord], source_query_ref: dict):
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    log_path = diagnostics_dir / "network_log.jsonl"
    def on_response(resp):
        url = resp.url
        lu = url.lower()
        if not any(k in lu for k in ["member", "search", "api", "ajax", "wp-json"]):
            return
        try:
            ctype = resp.headers.get("content-type", "").lower()
            if "json" in ctype:
                data = resp.json()
                recs = extract_records_from_json(data, source_query_ref.get("query", ""))
                network_records.extend(recs)
                with log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"url": url, "status": resp.status, "records": len(recs), "json_preview": data}, ensure_ascii=False)[:20000] + "\n")
            elif "text" in ctype or "html" in ctype:
                text = resp.text()[:100000]
                recs = parse_text_block(strip_html(text), source_query_ref.get("query", ""))
                if recs:
                    network_records.extend(recs)
                    with log_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"url": url, "status": resp.status, "records": len(recs), "text_preview": text[:3000]}, ensure_ascii=False) + "\n")
        except Exception:
            return
    page.on("response", on_response)


def scrape_members(url: str, deep: bool, max_prefix_len: int, wait_ms: int, diagnostics_dir: Path, limit_prefixes: int = 0) -> list[MemberRecord]:
    network_records: list[MemberRecord] = []
    source_query_ref = {"query": ""}
    all_records: list[MemberRecord] = []
    prefixes = make_prefixes(deep, max_prefix_len)
    if limit_prefixes:
        prefixes = prefixes[:limit_prefixes]
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            viewport={"width": 1400, "height": 1100},
        )
        page = context.new_page()
        setup_network_capture(page, diagnostics_dir, network_records, source_query_ref)
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        settle(page, wait_ms)
        all_records.extend(extract_dom_blocks(page, "initial"))
        for idx, prefix in enumerate(prefixes, start=1):
            source_query_ref["query"] = prefix
            try:
                recs = search_once(page, prefix, wait_ms)
                all_records.extend(recs)
                all_records.extend(network_records)
                all_records = unique(all_records)
                print(f"[{idx}/{len(prefixes)}] {prefix!r}: {len(recs)} visible, cache={len(all_records)}")
            except Exception as exc:
                print(f"[{idx}/{len(prefixes)}] {prefix!r}: ERROR {exc}")
                try:
                    page.screenshot(path=str(diagnostics_dir / f"error_{prefix}.png"), full_page=True)
                    (diagnostics_dir / f"error_{prefix}.html").write_text(page.content(), encoding="utf-8")
                except Exception:
                    pass
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    settle(page, wait_ms)
                except Exception:
                    pass
            time.sleep(0.15)
        browser.close()
    return unique(all_records + network_records)


def save_xlsx(records: list[MemberRecord], output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame([asdict(r) for r in records])
    if df.empty:
        df = pd.DataFrame(columns=["name", "company", "category", "source_query", "source_url", "raw_text"])
    df = df.drop_duplicates(subset=["name", "company", "category"]).sort_values(["name", "company"])
    df.to_excel(output, index=False)
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://www.rebny.com/members/")
    parser.add_argument("--output", default="data/rebny_members.xlsx")
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--max-prefix-len", type=int, default=2)
    parser.add_argument("--wait-ms", type=int, default=1200)
    parser.add_argument("--diagnostics-dir", default="diagnostics/rebny")
    parser.add_argument("--limit-prefixes", type=int, default=0)
    args = parser.parse_args()

    output = Path(args.output)
    diagnostics = Path(args.diagnostics_dir)
    records = scrape_members(
        url=args.url,
        deep=args.deep,
        max_prefix_len=args.max_prefix_len,
        wait_ms=args.wait_ms,
        diagnostics_dir=diagnostics,
        limit_prefixes=args.limit_prefixes,
    )
    df = save_xlsx(records, output)
    print(f"Wrote {len(df):,} records to {output}")
    if len(df) == 0:
        print(f"No records found. Check diagnostics in {diagnostics.resolve()}")

if __name__ == "__main__":
    main()
