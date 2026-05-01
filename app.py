from __future__ import annotations

from pathlib import Path
import pandas as pd
import streamlit as st

from vetting_core import (
    RebnyCache,
    dataframe_to_excel_bytes,
    lookup_fec,
    people_from_dataframe,
    read_table,
)

st.set_page_config(page_title="Donor Vetting", page_icon="🔎", layout="wide")

st.title("Donor Vetting")
st.caption("Upload names, check the local REBNY cache, optionally check OpenFEC, export XLSX.")

DEFAULT_CACHE = Path("data/rebny_members.xlsx")

with st.sidebar:
    st.header("Settings")
    run_rebny = st.checkbox("Check REBNY cache", value=True)
    rebny_file = st.file_uploader("Optional REBNY cache XLSX/CSV", type=["xlsx", "csv"], key="rebny_cache")
    run_fec = st.checkbox("Check FEC", value=False)
    fec_key = st.text_input("OpenFEC API key", type="password", value="")
    st.divider()
    st.write("Build REBNY cache locally:")
    st.code("python tools/download_rebny_members.py --output data/rebny_members.xlsx --deep", language="bash")

uploaded = st.file_uploader("Upload donor spreadsheet", type=["xlsx", "csv"])

if uploaded:
    try:
        donor_df = read_table(uploaded)
        people = people_from_dataframe(donor_df)
    except Exception as exc:
        st.error(str(exc))
        st.stop()

    st.success(f"Loaded {len(people)} people.")
    st.dataframe(pd.DataFrame([p.__dict__ for p in people]).head(20), use_container_width=True, hide_index=True)

    cache = None
    if run_rebny:
        try:
            if rebny_file is not None:
                cache = RebnyCache.from_file(rebny_file)
            elif DEFAULT_CACHE.exists():
                cache = RebnyCache.from_file(DEFAULT_CACHE)
            else:
                st.warning("No REBNY cache found. Run the downloader or upload data/rebny_members.xlsx in the sidebar.")
        except Exception as exc:
            st.error(f"Could not load REBNY cache: {exc}")
            st.stop()
        if cache is not None:
            st.info(f"REBNY cache loaded: {len(cache.records):,} records.")

    if st.button("Run vetting", type="primary"):
        rows = []
        progress = st.progress(0)
        for idx, person in enumerate(people, start=1):
            row = {
                "First Name": person.first_name,
                "Last Name": person.last_name,
                "State": person.state,
                "Zip": person.zip_code,
            }
            if run_rebny:
                if cache is None:
                    row.update({"REBNY Status": "NO CACHE", "REBNY Detail": "missing data/rebny_members.xlsx"})
                else:
                    row.update(cache.match_person(person).as_row())
            if run_fec:
                row.update(lookup_fec(person, fec_key).as_row())
            rows.append(row)
            progress.progress(idx / max(1, len(people)))
        progress.empty()
        result_df = pd.DataFrame(rows)
        st.subheader("Results")
        st.dataframe(result_df, use_container_width=True, hide_index=True)
        st.download_button(
            "Download results XLSX",
            data=dataframe_to_excel_bytes(result_df),
            file_name="vetted_results.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
