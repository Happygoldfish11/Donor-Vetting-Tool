import streamlit as st
import pandas as pd
import requests
import time
import io
from openpyxl import load_workbook, Workbook
from openpyxl.styles import PatternFill, Font
from openpyxl.utils import get_column_letter

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Donor Vetting Tool",
    page_icon="🔍",
    layout="centered",
)

# ── Styles ────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&family=DM+Mono:wght@400;500&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
}

.stApp {
    background-color: #0d1117;
    color: #e6edf3;
}

h1, h2, h3 { font-family: 'DM Sans', sans-serif; font-weight: 600; }

.hero {
    text-align: center;
    padding: 2.5rem 0 1.5rem;
}
.hero h1 {
    font-size: 2rem;
    color: #e6edf3;
    letter-spacing: -0.5px;
    margin-bottom: 0.4rem;
}
.hero p {
    color: #8b949e;
    font-size: 0.95rem;
    margin: 0;
}
.badge {
    display: inline-block;
    background: #1c2a3a;
    color: #58a6ff;
    border: 1px solid #30363d;
    border-radius: 20px;
    padding: 2px 12px;
    font-size: 0.75rem;
    font-family: 'DM Mono', monospace;
    margin-bottom: 1rem;
}
.card {
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
    padding: 1.5rem;
    margin: 1rem 0;
}
.stat-row {
    display: flex;
    gap: 1rem;
    margin: 1rem 0;
}
.stat {
    flex: 1;
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 1rem;
    text-align: center;
}
.stat .num { font-size: 1.8rem; font-weight: 600; font-family: 'DM Mono', monospace; }
.stat .label { font-size: 0.75rem; color: #8b949e; margin-top: 2px; text-transform: uppercase; letter-spacing: 0.5px; }
.flagged { color: #f85149; }
.clean { color: #3fb950; }
.unknown { color: #8b949e; }

.info-box {
    background: #1c2a3a;
    border-left: 3px solid #388bfd;
    border-radius: 0 6px 6px 0;
    padding: 0.75rem 1rem;
    font-size: 0.85rem;
    color: #8b949e;
    margin: 0.75rem 0;
}
</style>
""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div class="hero">
    <div class="badge">FEC · OpenFEC API</div>
    <h1>🔍 Donor Vetting Tool</h1>
    <p>Upload a spreadsheet of names — we'll check each one against FEC records<br>and flag donors to Republican candidates or aligned PACs.</p>
</div>
""", unsafe_allow_html=True)

# ── API Key input ─────────────────────────────────────────────────────────────
with st.expander("⚙️ API Settings", expanded=False):
    api_key_input = st.text_input(
        "OpenFEC API Key",
        type="password",
        placeholder="Paste your key from api.open.fec.gov/developers",
        help="Free key — 1,000 requests/hour. Get one at https://api.open.fec.gov/developers"
    )
    st.markdown('<div class="info-box">Your key is never stored or sent anywhere except directly to the FEC API.</div>', unsafe_allow_html=True)

api_key = api_key_input if api_key_input else "DEMO_KEY"

# ── Configuration ─────────────────────────────────────────────────────────────
FALSE_POSITIVE_THRESHOLD = 25
REPUBLICAN_PARTY_CODES = {"REP"}
REPUBLICAN_COMMITTEE_KEYWORDS = [
    "republican", "rnc", "nrcc", "nrsc", "gop", "maga",
    "america first", "trump", "conservative", "right to rise",
    "club for growth", "freedom works", "tea party", "heritage action",
    "citizens united", "crossroads", "american crossroads",
    "congressional leadership fund", "senate leadership fund", "israel"
]

def is_republican_recipient(committee_name: str, party: str) -> bool:
    if party and party.upper() in REPUBLICAN_PARTY_CODES:
        return True
    if committee_name:
        cn = committee_name.lower()
        return any(kw in cn for kw in REPUBLICAN_COMMITTEE_KEYWORDS)
    return False

def lookup_donor(first_name: str, last_name: str, api_key: str) -> dict:
    name_query = f"{last_name}, {first_name}".upper()
    url = "https://api.open.fec.gov/v1/schedules/schedule_a/"
    params = {
        "contributor_name": name_query,
        "two_year_transaction_period": [2026, 2024, 2022],
        "per_page": 100,
        "api_key": api_key,
        "sort": "-contribution_receipt_date",
    }

    # Default response structure for failures
    default_fail = {
        "status": "error", "flag": False, "needs_review": False,
        "total_contributions": 0, "republican_count": 0,
        "republican_total": 0, "top_recipients": "", "detail": ""
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 429:
            default_fail.update({"status": "rate_limited", "detail": "Rate limited"})
            return default_fail
        if resp.status_code != 200:
            default_fail.update({"status": "error", "detail": f"HTTP {resp.status_code}"})
            return default_fail

        data = resp.json()
        results = data.get("results", [])

        republican_donations = []
        for r in results:
            committee = r.get("committee", {}) or {}
            committee_name = committee.get("name", "") or r.get("committee_name", "") or ""
            party = committee.get("party", "") or ""
            amount = r.get("contribution_receipt_amount", 0) or 0
            if is_republican_recipient(committee_name, party):
                republican_donations.append({
                    "committee": committee_name,
                    "amount": amount,
                    "date": r.get("contribution_receipt_date", "")
                })

        flagged = len(republican_donations) > 0
        rep_total = sum(d["amount"] for d in republican_donations)
        top_recipients = list({d["committee"] for d in republican_donations})[:3]
        total_count = data.get("pagination", {}).get("count", len(results))
        needs_review = total_count >= FALSE_POSITIVE_THRESHOLD

        if needs_review and flagged:
            detail = f"{total_count} total FEC records — common name, verify manually. GOP: ${rep_total:,.0f}"
        elif flagged:
            detail = f"${rep_total:,.0f} across {len(republican_donations)} donation(s)"
        elif needs_review:
            detail = f"{total_count} total FEC records — common name, verify manually"
        else:
            detail = "No Republican donations found"

        return {
            "status": "ok",
            "flag": flagged,
            "needs_review": needs_review,
            "total_contributions": total_count,
            "republican_count": len(republican_donations),
            "republican_total": rep_total,
            "top_recipients": ", ".join(top_recipients) if top_recipients else "",
            "detail": detail
        }

    except requests.exceptions.Timeout:
        default_fail.update({"status": "timeout", "detail": "Request timed out"})
        return default_fail
    except Exception as e:
        default_fail.update({"detail": str(e)})
        return default_fail

# ── File upload ───────────────────────────────────────────────────────────────
st.markdown('<div class="card">', unsafe_allow_html=True)
st.markdown("### 📂 Upload Your Spreadsheet")
st.markdown('<div class="info-box">Your file must have a <b>First Name</b> and <b>Last Name</b> column.</div>', unsafe_allow_html=True)

uploaded = st.file_uploader("", type=["xlsx", "csv"], label_visibility="collapsed")
st.markdown('</div>', unsafe_allow_html=True)

if uploaded:
    try:
        if uploaded.name.endswith(".csv"):
            df = pd.read_csv(uploaded)
        else:
            df = pd.read_excel(uploaded)
    except Exception as e:
        st.error(f"Could not read file: {e}")
        st.stop()

    df.columns = [c.strip().lower() for c in df.columns]
    col_map = {}
    for c in df.columns:
        if "first" in c: col_map["first"] = c
        if "last" in c: col_map["last"] = c

    if "first" not in col_map or "last" not in col_map:
        st.error("❌ Couldn't find 'First Name' and 'Last Name' columns.")
        st.stop()

    df_preview = df[[col_map["first"], col_map["last"]]].copy()
    df_preview.columns = ["First Name", "Last Name"]

    st.markdown(f"**{len(df_preview)} names loaded.**")
    st.dataframe(df_preview.head(5), use_container_width=True, hide_index=True)

    if st.button("🔍 Run Vetting", type="primary", use_container_width=True):
        results_list = []
        progress = st.progress(0, text="Starting...")
        status_box = st.empty()
        total = len(df_preview)

        for i, row in df_preview.iterrows():
            first = str(row["First Name"]).strip()
            last = str(row["Last Name"]).strip()
            status_box.markdown(f'<div class="info-box">Checking <b>{first} {last}</b> ({i+1} of {total})...</div>', unsafe_allow_html=True)

            result = lookup_donor(first, last, api_key)
            result["First Name"] = first
            result["Last Name"] = last
            results_list.append(result)

            progress.progress((i + 1) / total, text=f"{i+1}/{total} checked")
            time.sleep(0.15)

        status_box.empty()
        progress.empty()

        def status_label(r):
            if r.get("needs_review") and r.get("flag"):
                return "FLAGGED — REVIEW"
            elif r.get("flag"):
                return "FLAGGED"
            elif r.get("needs_review"):
                return "REVIEW NEEDED"
            else:
                return "Clean"

        out_df = df_preview.copy()
        out_df["Status"] = [status_label(r) for r in results_list]
        out_df["GOP Donations ($)"] = [f"${r['republican_total']:,.0f}" if r.get("flag") else "—" for r in results_list]
        out_df["Top Recipients"] = [r.get("top_recipients", "") for r in results_list]
        out_df["FEC Records Found"] = [r.get("total_contributions", 0) for r in results_list]
        out_df["Detail"] = [r.get("detail", "") for r in results_list]

        flagged_count = sum(1 for r in results_list if r.get("flag") and not r.get("needs_review"))
        review_count = sum(1 for r in results_list if r.get("needs_review"))
        clean_count = total - flagged_count - review_count

        st.markdown(f"""
        <div class="stat-row">
            <div class="stat"><div class="num">{total}</div><div class="label">Total Checked</div></div>
            <div class="stat"><div class="num flagged">{flagged_count}</div><div class="label">Flagged</div></div>
            <div class="stat"><div class="num" style="color:#e3b341">{review_count}</div><div class="label">Review Needed</div></div>
            <div class="stat"><div class="num clean">{clean_count}</div><div class="label">Clean</div></div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("### Results")
        st.dataframe(out_df, use_container_width=True, hide_index=True)

        # ── Excel Export ──────────────────────────────────────────────────────
        uploaded.seek(0)
        if uploaded.name.endswith(".xlsx"):
            wb = load_workbook(uploaded)
            ws = wb.active
        else:
            wb = Workbook()
            ws = wb.active
            ws.append(list(df_preview.columns))
            for row in df_preview.itertuples(index=False):
                ws.append(list(row))

        flag_col = ws.max_column + 1
        headers = ["Status", "GOP Donations ($)", "Top Recipients", "FEC Records Found"]
        for idx, h in enumerate(headers):
            cell = ws.cell(1, flag_col + idx)
            cell.value = h
            cell.font = Font(bold=True)

        red_fill = PatternFill("solid", start_color="FFCCCC")
        orange_fill = PatternFill("solid", start_color="FFE5B4")
        green_fill = PatternFill("solid", start_color="CCFFCC")

        for i, r in enumerate(results_list, start=2):
            label = status_label(r)
            ws.cell(i, flag_col).value = label
            ws.cell(i, flag_col + 1).value = f"${r['republican_total']:,.0f}" if r.get("flag") else ""
            ws.cell(i, flag_col + 2).value = r.get("top_recipients", "")
            ws.cell(i, flag_col + 3).value = r.get("total_contributions", 0)

            if r.get("flag") and not r.get("needs_review"):
                fill = red_fill
            elif r.get("needs_review"):
                fill = orange_fill
            else:
                fill = green_fill

            for c in range(1, flag_col + 4):
                ws.cell(i, c).fill = fill

        for c in range(flag_col, flag_col + 4):
            ws.column_dimensions[get_column_letter(c)].width = 22

        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)

        st.download_button(
            label="⬇️ Download Vetted Spreadsheet",
            data=buf,
            file_name=f"{uploaded.name.rsplit('.', 1)[0]}_vetted.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary"
        )
