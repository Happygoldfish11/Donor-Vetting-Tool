from __future__ import annotations

import io
import time
from dataclasses import asdict
from typing import Any

import pandas as pd
import streamlit as st

from donor_vetting.batch import people_from_dataframe, people_preview_rows
from donor_vetting.excel import dataframe_to_excel_bytes
from donor_vetting.fec import lookup_donor
from donor_vetting.models import Person
from donor_vetting.rebny import lookup_rebny

st.set_page_config(page_title="Donor Vetting Tool", page_icon="🔍", layout="wide")

st.markdown(
    """
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&family=DM+Mono:wght@400;500&display=swap');
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
.stApp { background: #0d1117; color: #e6edf3; }
h1, h2, h3 { font-weight: 700; letter-spacing: -0.02em; }
.hero { text-align:center; padding: 2rem 0 1rem; }
.hero h1 { font-size: 2.4rem; margin: .3rem 0; color:#e6edf3; }
.hero p { color:#8b949e; margin:0; font-size:1rem; }
.badge { display:inline-block; color:#58a6ff; background:#10243a; border:1px solid #30363d; border-radius:999px; padding:.2rem .75rem; font-family:'DM Mono', monospace; font-size:.78rem; }
.info-box { background:#142033; border-left: 3px solid #388bfd; color:#b9c4d0; border-radius:0 8px 8px 0; padding:.85rem 1rem; margin:.75rem 0; }
.small-muted { color:#8b949e; font-size:.86rem; }
.metric-card { background:#161b22; border:1px solid #30363d; border-radius:14px; padding:1rem; }
</style>
""",
    unsafe_allow_html=True,
)

st.markdown(
    """
<div class="hero">
  <div class="badge">OpenFEC · Public REBNY Member Directory · Exact-match vetting</div>
  <h1>🔍 Donor Vetting Tool</h1>
  <p>Upload names, check federal campaign contributions, and verify REBNY membership without count-based false positives.</p>
</div>
""",
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Settings")
    st.caption("API keys are used only in this browser session.")
    api_key = st.text_input("OpenFEC API key", type="password", placeholder="DEMO_KEY", help="Get a free key from api.open.fec.gov/developers.") or "DEMO_KEY"

    st.subheader("Checks")
    run_fec = st.checkbox("FEC Republican donation check", value=True)
    run_rebny = st.checkbox("REBNY public member-directory check", value=True)

    st.subheader("FEC scope")
    cycles = st.multiselect(
        "Election cycles",
        options=[2026, 2024, 2022, 2020, 2018, 2016, 2014, 2012],
        default=[2026, 2024, 2022],
    )
    max_pages = st.slider("Max FEC pages per person", min_value=1, max_value=100, value=50, help="100 records per page. Increase for very common names.")
    common_name_threshold = st.slider("Review threshold for common names", min_value=5, max_value=100, value=25)

    st.subheader("Politeness")
    rebny_delay = st.number_input("REBNY delay per lookup (seconds)", min_value=0.0, max_value=5.0, value=0.35, step=0.05)
    fec_delay = st.number_input("FEC delay per lookup (seconds)", min_value=0.0, max_value=5.0, value=0.05, step=0.05)

st.markdown("### 1) Upload spreadsheet")
st.markdown(
    '<div class="info-box">Accepted columns: <b>First Name</b> + <b>Last Name</b>, or one <b>Full Name</b> column. Optional disambiguators: <b>State</b>, <b>Zip</b>, <b>Employer</b>, <b>Occupation</b>.</div>',
    unsafe_allow_html=True,
)

uploaded = st.file_uploader("Upload CSV or XLSX", type=["csv", "xlsx"], label_visibility="collapsed")


def read_uploaded_file(file: Any) -> pd.DataFrame:
    if file.name.lower().endswith(".csv"):
        return pd.read_csv(file)
    return pd.read_excel(file)


def result_row_base(person: Person) -> dict[str, Any]:
    return {
        "First Name": person.first_name,
        "Last Name": person.last_name,
        "State": person.state,
        "Zip": person.zip_code,
        "Employer": person.employer,
        "Occupation": person.occupation,
    }


def run_checks(people: list[Person]) -> pd.DataFrame:
    progress = st.progress(0, text="Starting vetting...")
    status = st.empty()
    rows: list[dict[str, Any]] = []
    total = len(people)

    for index, person in enumerate(people, start=1):
        status.markdown(
            f'<div class="info-box">Checking <b>{person.full_name}</b> ({index} of {total})...</div>',
            unsafe_allow_html=True,
        )
        row = result_row_base(person)

        if run_fec:
            fec = lookup_donor(
                person,
                api_key,
                cycles=cycles,
                max_pages=max_pages,
                false_positive_threshold=common_name_threshold,
            )
            row.update(fec.as_row())
            if fec_delay:
                time.sleep(fec_delay)

        if run_rebny:
            rebny = lookup_rebny(
                person.first_name,
                person.last_name,
                polite_delay_seconds=rebny_delay,
                use_playwright_fallback=True,
            )
            row.update(rebny.as_row())

        rows.append(row)
        progress.progress(index / total, text=f"{index}/{total} checked")

    status.empty()
    progress.empty()
    return pd.DataFrame(rows)


if uploaded is not None:
    try:
        df = read_uploaded_file(uploaded)
        people = people_from_dataframe(df)
    except Exception as exc:
        st.error(f"Could not read names: {exc}")
        st.stop()

    st.success(f"Loaded {len(people)} people.")
    with st.expander("Preview parsed names", expanded=True):
        st.dataframe(pd.DataFrame(people_preview_rows(people)), use_container_width=True, hide_index=True)

    if not run_fec and not run_rebny:
        st.warning("Select at least one check in the sidebar.")
        st.stop()

    st.markdown("### 2) Run vetting")
    st.markdown(
        "<p class='small-muted'>REBNY FOUND means the returned member name itself matched first+last tokens. Count-only directory responses are never marked as matches.</p>",
        unsafe_allow_html=True,
    )

    if st.button("🔍 Run Vetting", type="primary", use_container_width=True):
        out_df = run_checks(people)

        st.markdown("### 3) Summary")
        cols = st.columns(5)
        cols[0].metric("Total checked", len(out_df))
        if run_fec:
            fec_flagged = int(out_df["FEC Status"].astype(str).str.contains("FLAGGED", na=False).sum())
            fec_review = int(out_df["FEC Status"].astype(str).str.contains("REVIEW", na=False).sum())
            cols[1].metric("FEC flagged", fec_flagged)
            cols[2].metric("FEC review", fec_review)
        if run_rebny:
            rebny_found = int((out_df["REBNY Status"].astype(str) == "FOUND").sum())
            rebny_review = int((out_df["REBNY Status"].astype(str) == "review").sum())
            cols[3].metric("REBNY found", rebny_found)
            cols[4].metric("REBNY review", rebny_review)

        st.markdown("### Results")
        st.dataframe(out_df, use_container_width=True, hide_index=True)

        csv_bytes = out_df.to_csv(index=False).encode("utf-8")
        xlsx_bytes = dataframe_to_excel_bytes(out_df)
        left, right = st.columns(2)
        left.download_button(
            "⬇️ Download CSV",
            data=csv_bytes,
            file_name=f"{uploaded.name.rsplit('.', 1)[0]}_vetted.csv",
            mime="text/csv",
            use_container_width=True,
        )
        right.download_button(
            "⬇️ Download Excel",
            data=xlsx_bytes,
            file_name=f"{uploaded.name.rsplit('.', 1)[0]}_vetted.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary",
        )
else:
    st.markdown("### What changed")
    st.markdown(
        """
- **REBNY is now card/name based, not count based.** A page saying “1 Members” is not enough; the app extracts candidate names and checks them against the searched person.
- **Ambiguous REBNY results go to review.** Initial-only or fuzzy matches are not silently treated as confirmed members.
- **FEC now paginates.** The old version only looked at the first 100 records.
- **FEC donor names are re-filtered locally.** This reduces false positives from broad FEC contributor-name search.
- **The Republican classifier is stricter.** Generic issue terms were removed from the Republican keyword list.
"""
    )
