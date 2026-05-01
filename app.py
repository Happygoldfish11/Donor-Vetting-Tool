import streamlit as st
import pandas as pd
import requests
import time
import io
import re
from openpyxl import load_workbook, Workbook
from openpyxl.styles import PatternFill, Font
from openpyxl.utils import get_column_letter

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# --------------------------------------------------------------------------- #
# Page config
# --------------------------------------------------------------------------- #
st.set_page_config(
    page_title="Donor Vetting Tool",
    page_icon=":mag:",
    layout="centered",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap');
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
.stApp { background-color: #0d1117; color: #e6edf3; }
h1, h2, h3 { font-family: 'DM Sans', sans-serif; font-weight: 600; }
.hero { text-align: center; padding: 2.5rem 0 1.5rem; }
.hero h1 { font-size: 2rem; color: #e6edf3; letter-spacing: -0.5px; margin-bottom: 0.4rem; }
.hero p { color: #8b949e; font-size: 0.95rem; margin: 0; }
.badge {
    display: inline-block; background: #1c2a3a; color: #58a6ff;
    border: 1px solid #30363d; border-radius: 20px; padding: 2px 12px;
    font-size: 0.75rem; font-family: 'DM Mono', monospace; margin-bottom: 1rem;
}
.card { background: #161b22; border: 1px solid #30363d; border-radius: 10px; padding: 1.5rem; margin: 1rem 0; }
.stat-row { display: flex; gap: 1rem; margin: 1rem 0; }
.stat { flex: 1; background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 1rem; text-align: center; }
.stat .num { font-size: 1.8rem; font-weight: 600; font-family: 'DM Mono', monospace; }
.stat .label { font-size: 0.75rem; color: #8b949e; margin-top: 2px; text-transform: uppercase; letter-spacing: 0.5px; }
.flagged { color: #f85149; }
.clean { color: #3fb950; }
.info-box {
    background: #1c2a3a; border-left: 3px solid #388bfd;
    border-radius: 0 6px 6px 0; padding: 0.75rem 1rem;
    font-size: 0.85rem; color: #8b949e; margin: 0.75rem 0;
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
    <div class="badge">FEC &middot; OpenFEC API &middot; REBNY</div>
    <h1>&#128269; Donor Vetting Tool</h1>
    <p>Upload a spreadsheet of names &mdash; check FEC records for Republican donations<br>and look up REBNY membership.</p>
</div>
""", unsafe_allow_html=True)

# --------------------------------------------------------------------------- #
# API key
# --------------------------------------------------------------------------- #
with st.expander("API Settings", expanded=False):
    api_key_input = st.text_input(
        "OpenFEC API Key",
        type="password",
        placeholder="Paste your key from api.open.fec.gov/developers",
        help="Free key - 1,000 requests/hour.",
    )
    st.markdown(
        '<div class="info-box">Your key is sent directly to the FEC API and never stored.</div>',
        unsafe_allow_html=True,
    )

api_key = api_key_input.strip() if api_key_input else "DEMO_KEY"

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
FALSE_POSITIVE_THRESHOLD = 25
REPUBLICAN_PARTY_CODES = {"REP"}
REPUBLICAN_COMMITTEE_KEYWORDS = [
    "republican", "rnc", "nrcc", "nrsc", "gop", "maga",
    "america first", "trump", "conservative", "right to rise",
    "club for growth", "freedom works", "tea party", "heritage action",
    "citizens united", "crossroads", "american crossroads",
    "congressional leadership fund", "senate leadership fund",
]

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def is_republican_recipient(committee_name, party):
    if party and party.upper() in REPUBLICAN_PARTY_CODES:
        return True
    if committee_name:
        cn = committee_name.lower()
        return any(kw in cn for kw in REPUBLICAN_COMMITTEE_KEYWORDS)
    return False


# --------------------------------------------------------------------------- #
# FEC lookup
# --------------------------------------------------------------------------- #
def lookup_donor(first_name, last_name, api_key):
    name_query = f"{last_name}, {first_name}".upper()
    url = "https://api.open.fec.gov/v1/schedules/schedule_a/"
    params = {
        "contributor_name": name_query,
        "two_year_transaction_period": [2026, 2024, 2022],
        "per_page": 100,
        "api_key": api_key,
        "sort": "-contribution_receipt_date",
    }
    fail = {
        "fec_status": "error", "flag": False, "needs_review": False,
        "total_contributions": 0, "republican_count": 0,
        "republican_total": 0, "top_recipients": "", "fec_detail": "",
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 429:
            fail.update({"fec_status": "rate_limited", "fec_detail": "Rate limited"})
            return fail
        if resp.status_code != 200:
            fail.update({"fec_status": "error", "fec_detail": f"HTTP {resp.status_code}"})
            return fail

        data = resp.json()
        results = data.get("results", [])
        republican_donations = []

        for r in results:
            committee = r.get("committee", {}) or {}
            committee_name = committee.get("name", "") or r.get("committee_name", "") or ""
            party = committee.get("party", "") or ""
            amount = r.get("contribution_receipt_amount", 0) or 0
            if is_republican_recipient(committee_name, party):
                republican_donations.append({"committee": committee_name, "amount": amount})

        flagged = len(republican_donations) > 0
        rep_total = sum(d["amount"] for d in republican_donations)
        top_recipients = list({d["committee"] for d in republican_donations})[:3]
        total_count = data.get("pagination", {}).get("count", len(results))
        needs_review = total_count >= FALSE_POSITIVE_THRESHOLD

        if needs_review and flagged:
            detail = f"{total_count} total FEC records - common name, verify manually. GOP: ${rep_total:,.0f}"
        elif flagged:
            detail = f"${rep_total:,.0f} across {len(republican_donations)} donation(s)"
        elif needs_review:
            detail = f"{total_count} total FEC records - common name, verify manually"
        else:
            detail = "No Republican donations found"

        return {
            "fec_status": "ok",
            "flag": flagged,
            "needs_review": needs_review,
            "total_contributions": total_count,
            "republican_count": len(republican_donations),
            "republican_total": rep_total,
            "top_recipients": ", ".join(top_recipients) if top_recipients else "",
            "fec_detail": detail,
        }

    except requests.exceptions.Timeout:
        fail.update({"fec_status": "timeout", "fec_detail": "Request timed out"})
        return fail
    except Exception as e:
        fail.update({"fec_detail": str(e)})
        return fail


# --------------------------------------------------------------------------- #
# REBNY lookup
# --------------------------------------------------------------------------- #
def clean_person_text(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()


def rebny_visible_name_match(page_text, first_name, last_name):
    first = clean_person_text(first_name)
    last = clean_person_text(last_name)

    if not first or not last:
        return False

    first_last = re.compile(rf"\b{re.escape(first)}\b.*\b{re.escape(last)}\b")
    last_first = re.compile(rf"\b{re.escape(last)}\b.*\b{re.escape(first)}\b")

    for line in page_text.splitlines():
        line = clean_person_text(line)
        if first_last.search(line) or last_first.search(line):
            return True

    return False


def lookup_rebny(first_name, last_name):
    """
    Uses a headless Chromium browser (Playwright) to search the REBNY member
    directory, which is JavaScript-rendered and cannot be scraped with plain
    HTTP requests.

    One-time setup (run in your terminal before starting Streamlit):
        pip install playwright
        playwright install chromium
    """
    default = {
        "rebny_status": "unknown",
        "rebny_match": False,
        "rebny_detail": "Could not reach REBNY",
    }

    if not PLAYWRIGHT_AVAILABLE:
        default["rebny_status"] = "unavailable"
        default["rebny_detail"] = (
            "Playwright not installed. Run: pip install playwright && playwright install chromium"
        )
        return default

    query = f"{first_name} {last_name}"
    encoded = requests.utils.quote(query)
    url = f"https://www.rebny.com/members/?search={encoded}"

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=25000)

            # Wait up to 8 s for the directory area to finish rendering.
            try:
                page.wait_for_selector(
                    "text=/No Search Results|Member Directory|Members?/",
                    timeout=8000,
                )
            except Exception:
                pass

            try:
                page_text = page.locator("body").inner_text(timeout=5000)
            except Exception:
                page_text = page.content()

            browser.close()

        count_match = re.search(r"(\d+)\s*Members?", page_text, re.IGNORECASE)
        count = int(count_match.group(1)) if count_match else 0

        # Do not trust the generic page count by itself. The old code marked
        # everyone as a member when the page contained text like "3 Members".
        # Only mark FOUND when the searched person's name is visible in the result text.
        if rebny_visible_name_match(page_text, first_name, last_name):
            detail = "Name appears in REBNY directory results"
            if count > 0:
                detail = f"Name appears in REBNY directory results ({count} result(s) shown)"
            return {
                "rebny_status": "FOUND",
                "rebny_match": True,
                "rebny_detail": detail,
            }

        if re.search(r"No Search Results", page_text, re.IGNORECASE) or count == 0:
            return {
                "rebny_status": "not found",
                "rebny_match": False,
                "rebny_detail": "Not in REBNY directory",
            }

        return {
            "rebny_status": "review",
            "rebny_match": False,
            "rebny_detail": (
                f"{count} directory result(s) shown, but {query} was not visible as an exact result"
            ),
        }

    except Exception as e:
        default["rebny_detail"] = f"Error: {e}"
        return default


# --------------------------------------------------------------------------- #
# Status helpers
# --------------------------------------------------------------------------- #
def fec_status_label(r):
    if r.get("needs_review") and r.get("flag"):
        return "FLAGGED - REVIEW"
    if r.get("flag"):
        return "FLAGGED"
    if r.get("needs_review"):
        return "REVIEW NEEDED"
    return "Clean"


# --------------------------------------------------------------------------- #
# File upload UI
# --------------------------------------------------------------------------- #
st.markdown('<div class="card">', unsafe_allow_html=True)
st.markdown("### Upload Your Spreadsheet")
st.markdown(
    '<div class="info-box">File must have <b>First Name</b> and <b>Last Name</b> columns. CSV or XLSX accepted.</div>',
    unsafe_allow_html=True,
)
uploaded = st.file_uploader("", type=["xlsx", "csv"], label_visibility="collapsed")
st.markdown("</div>", unsafe_allow_html=True)

if uploaded:
    try:
        df = pd.read_csv(uploaded) if uploaded.name.endswith(".csv") else pd.read_excel(uploaded)
    except Exception as e:
        st.error(f"Could not read file: {e}")
        st.stop()

    df.columns = [c.strip().lower() for c in df.columns]
    col_map = {}
    for c in df.columns:
        if "first" in c:
            col_map["first"] = c
        if "last" in c:
            col_map["last"] = c

    if "first" not in col_map or "last" not in col_map:
        st.error("Could not find 'First Name' and 'Last Name' columns in your file.")
        st.stop()

    df_names = df[[col_map["first"], col_map["last"]]].copy()
    df_names.columns = ["First Name", "Last Name"]

    st.markdown(f"**{len(df_names)} names loaded.**")
    st.dataframe(df_names.head(5), use_container_width=True, hide_index=True)

    st.markdown("**Checks to run:**")
    run_fec   = st.checkbox("FEC Republican donation check", value=True)
    run_rebny = st.checkbox("REBNY member directory check", value=True)

    if not PLAYWRIGHT_AVAILABLE and run_rebny:
        st.warning(
            "Playwright is not installed - REBNY check will be skipped. "
            "Run `pip install playwright && playwright install chromium` then restart Streamlit."
        )

    if st.button("Run Vetting", type="primary", use_container_width=True):
        if not run_fec and not run_rebny:
            st.warning("Select at least one check to run.")
            st.stop()

        results_list = []
        progress  = st.progress(0, text="Starting...")
        status_box = st.empty()
        total = len(df_names)

        for idx, row in df_names.iterrows():
            first = str(row["First Name"]).strip()
            last  = str(row["Last Name"]).strip()
            status_box.markdown(
                f'<div class="info-box">Checking <b>{first} {last}</b> ({idx + 1} of {total})...</div>',
                unsafe_allow_html=True,
            )

            result = {"First Name": first, "Last Name": last}

            if run_fec:
                result.update(lookup_donor(first, last, api_key))
                time.sleep(0.15)

            if run_rebny and PLAYWRIGHT_AVAILABLE:
                result.update(lookup_rebny(first, last))
                time.sleep(0.3)

            results_list.append(result)
            progress.progress((idx + 1) / total, text=f"{idx + 1}/{total} checked")

        status_box.empty()
        progress.empty()

        # Build output dataframe
        out_df = df_names.copy()

        if run_fec:
            out_df["FEC Status"]       = [fec_status_label(r) for r in results_list]
            out_df["GOP Donations ($)"] = [
                f"${r['republican_total']:,.0f}" if r.get("flag") else "-"
                for r in results_list
            ]
            out_df["Top Recipients"]   = [r.get("top_recipients", "") for r in results_list]
            out_df["FEC Records"]      = [r.get("total_contributions", 0) for r in results_list]
            out_df["FEC Detail"]       = [r.get("fec_detail", "") for r in results_list]

        if run_rebny and PLAYWRIGHT_AVAILABLE:
            out_df["REBNY Member?"] = [
                "YES" if r.get("rebny_match") else r.get("rebny_status", "unknown")
                for r in results_list
            ]
            out_df["REBNY Detail"]  = [r.get("rebny_detail", "") for r in results_list]

        # Summary stats
        stat_html = (
            f'<div class="stat-row">'
            f'<div class="stat"><div class="num">{total}</div>'
            f'<div class="label">Total Checked</div></div>'
        )

        if run_fec:
            flagged_count = sum(1 for r in results_list if r.get("flag") and not r.get("needs_review"))
            review_count  = sum(1 for r in results_list if r.get("needs_review"))
            clean_count   = total - flagged_count - review_count
            stat_html += (
                f'<div class="stat"><div class="num flagged">{flagged_count}</div>'
                f'<div class="label">FEC Flagged</div></div>'
                f'<div class="stat"><div class="num" style="color:#e3b341">{review_count}</div>'
                f'<div class="label">FEC Review</div></div>'
                f'<div class="stat"><div class="num clean">{clean_count}</div>'
                f'<div class="label">FEC Clean</div></div>'
            )

        if run_rebny and PLAYWRIGHT_AVAILABLE:
            rebny_found = sum(1 for r in results_list if r.get("rebny_match"))
            stat_html += (
                f'<div class="stat"><div class="num" style="color:#d2a8ff">{rebny_found}</div>'
                f'<div class="label">REBNY Members</div></div>'
            )

        stat_html += "</div>"
        st.markdown(stat_html, unsafe_allow_html=True)

        st.markdown("### Results")
        st.dataframe(out_df, use_container_width=True, hide_index=True)

        # Excel export
        uploaded.seek(0)
        if uploaded.name.endswith(".xlsx"):
            wb = load_workbook(uploaded)
            ws = wb.active
        else:
            wb = Workbook()
            ws = wb.active
            ws.append(list(df_names.columns))
            for row in df_names.itertuples(index=False):
                ws.append(list(row))

        flag_col = ws.max_column + 1
        export_headers = []
        if run_fec:
            export_headers += ["FEC Status", "GOP Donations ($)", "Top Recipients", "FEC Records", "FEC Detail"]
        if run_rebny and PLAYWRIGHT_AVAILABLE:
            export_headers += ["REBNY Member?", "REBNY Detail"]

        for i, h in enumerate(export_headers):
            cell = ws.cell(1, flag_col + i)
            cell.value = h
            cell.font  = Font(bold=True)

        red_fill    = PatternFill("solid", start_color="FFCCCC")
        orange_fill = PatternFill("solid", start_color="FFE5B4")
        green_fill  = PatternFill("solid", start_color="CCFFCC")
        purple_fill = PatternFill("solid", start_color="E8D5FF")

        for i, r in enumerate(results_list, start=2):
            offset = 0
            if run_fec:
                ws.cell(i, flag_col + offset    ).value = fec_status_label(r)
                ws.cell(i, flag_col + offset + 1).value = f"${r['republican_total']:,.0f}" if r.get("flag") else ""
                ws.cell(i, flag_col + offset + 2).value = r.get("top_recipients", "")
                ws.cell(i, flag_col + offset + 3).value = r.get("total_contributions", 0)
                ws.cell(i, flag_col + offset + 4).value = r.get("fec_detail", "")
                offset += 5

            if run_rebny and PLAYWRIGHT_AVAILABLE:
                ws.cell(i, flag_col + offset    ).value = "FOUND" if r.get("rebny_match") else r.get("rebny_status", "")
                ws.cell(i, flag_col + offset + 1).value = r.get("rebny_detail", "")

            if run_fec and r.get("flag") and not r.get("needs_review"):
                fill = red_fill
            elif run_fec and r.get("needs_review"):
                fill = orange_fill
            elif run_rebny and r.get("rebny_match"):
                fill = purple_fill
            else:
                fill = green_fill

            for c in range(1, flag_col + len(export_headers)):
                ws.cell(i, c).fill = fill

        for c in range(flag_col, flag_col + len(export_headers)):
            ws.column_dimensions[get_column_letter(c)].width = 24

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        base_name = uploaded.name.rsplit(".", 1)[0]
        st.download_button(
            label="Download Vetted Spreadsheet",
            data=buf,
            file_name=f"{base_name}_vetted.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary",
        )
