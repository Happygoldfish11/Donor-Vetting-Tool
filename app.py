from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from vetting_core import (
    DEFAULT_FEC_YEARS,
    DEFAULT_REBNY_CACHE_PATH,
    build_results_dataframe,
    dataframe_to_xlsx_bytes,
    load_rebny_members,
    lookup_fec,
    lookup_rebny_from_members,
    people_from_dataframe,
    people_preview_rows,
    read_spreadsheet,
    results_summary,
)

st.set_page_config(page_title="Donor Vetting Tool", page_icon="🔍", layout="wide")

st.markdown(
    """
    <style>
    .main .block-container { padding-top: 2rem; max-width: 1200px; }
    .small-muted { color: #6b7280; font-size: 0.9rem; }
    .metric-card { border: 1px solid #e5e7eb; border-radius: 12px; padding: 1rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🔍 Donor Vetting Tool")
st.caption("Upload names, check OpenFEC records, and match against a local REBNY member cache.")

with st.sidebar:
    st.header("Settings")
    api_key = st.text_input(
        "OpenFEC API key",
        type="password",
        value=st.secrets.get("FEC_API_KEY", "") if hasattr(st, "secrets") else "",
        help="Leave blank to use DEMO_KEY. A personal key is more reliable for larger files.",
    )

    years_text = st.text_input(
        "FEC two-year periods",
        value=", ".join(str(year) for year in DEFAULT_FEC_YEARS),
        help="Example: 2026, 2024, 2022",
    )

    run_fec = st.checkbox("Run FEC check", value=True)
    run_rebny = st.checkbox("Run REBNY check", value=True)

    st.divider()
    st.subheader("REBNY cache")
    cache_upload = st.file_uploader(
        "Optional: upload rebny_members.xlsx",
        type=["xlsx", "csv"],
        help="If not uploaded, the app uses data/rebny_members.xlsx in the repo.",
    )

    cache_exists = DEFAULT_REBNY_CACHE_PATH.exists()
    if cache_upload:
        st.success("Using uploaded REBNY cache.")
    elif cache_exists:
        st.success(f"Using {DEFAULT_REBNY_CACHE_PATH}.")
    else:
        st.warning("No REBNY cache found yet.")
        st.code("python tools/download_rebny_members.py --output data/rebny_members.xlsx --deep")

uploaded = st.file_uploader("Upload donor spreadsheet", type=["xlsx", "csv"])

if not uploaded:
    st.info("Your file needs First Name and Last Name columns. Optional State and Zip columns improve FEC matching.")
    st.stop()

try:
    source_df = read_spreadsheet(uploaded)
    people, column_map = people_from_dataframe(source_df)
except Exception as exc:
    st.error(str(exc))
    st.stop()

if not people:
    st.error("No people found in the uploaded file.")
    st.stop()

st.subheader("Preview")
st.write(f"Loaded **{len(people)}** people.")
st.dataframe(people_preview_rows(people), use_container_width=True, hide_index=True)

try:
    fec_years = [int(part.strip()) for part in years_text.split(",") if part.strip()]
except ValueError:
    st.error("FEC years must be comma-separated numbers, like: 2026, 2024, 2022")
    st.stop()

if st.button("Run vetting", type="primary", use_container_width=True):
    if not run_fec and not run_rebny:
        st.warning("Select at least one check.")
        st.stop()

    rebny_members = []
    if run_rebny:
        with st.spinner("Loading REBNY cache..."):
            rebny_members = load_rebny_members(cache_upload if cache_upload else DEFAULT_REBNY_CACHE_PATH)
        if not rebny_members:
            st.warning("REBNY check will return REVIEW until a cache is uploaded or committed to data/rebny_members.xlsx.")

    progress = st.progress(0)
    status = st.empty()

    rebny_results = {} if run_rebny else None
    fec_results = {} if run_fec else None

    for number, person in enumerate(people, start=1):
        status.write(f"Checking {person.full_name} ({number}/{len(people)})")

        if run_rebny and rebny_results is not None:
            rebny_results[person.original_index] = lookup_rebny_from_members(person, rebny_members)

        if run_fec and fec_results is not None:
            fec_results[person.original_index] = lookup_fec(
                person,
                api_key=api_key or "DEMO_KEY",
                years=fec_years,
                max_pages=5,
                pause_seconds=0.1,
            )

        progress.progress(number / len(people))

    status.empty()
    progress.empty()

    out_df = build_results_dataframe(
        source_df,
        people,
        column_map,
        rebny_results=rebny_results,
        fec_results=fec_results,
    )

    summary = results_summary(people, rebny_results=rebny_results, fec_results=fec_results)
    cols = st.columns(5)
    cols[0].metric("Total checked", summary.get("total", 0))
    if run_fec:
        cols[1].metric("FEC flagged", summary.get("fec_flagged", 0))
        cols[2].metric("FEC review", summary.get("fec_review", 0))
    if run_rebny:
        cols[3].metric("REBNY found", summary.get("rebny_found", 0))
        cols[4].metric("REBNY review", summary.get("rebny_review", 0))

    st.subheader("Results")
    st.dataframe(out_df, use_container_width=True, hide_index=True)

    xlsx_bytes = dataframe_to_xlsx_bytes(out_df)
    output_name = Path(uploaded.name).stem + "_vetted.xlsx"
    st.download_button(
        "Download vetted spreadsheet",
        data=xlsx_bytes,
        file_name=output_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
        use_container_width=True,
    )
