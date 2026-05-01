# Donor Vetting Tool

Streamlit app for checking uploaded names against:

1. OpenFEC Schedule A contribution records
2. A local REBNY member cache stored as `data/rebny_members.xlsx`

## Repo layout

```text
.
├── app.py
├── vetting_core.py
├── requirements.txt
├── requirements-scraper.txt
├── requirements-dev.txt
├── tools/
│   └── download_rebny_members.py
├── tests/
├── data/
│   └── .gitkeep
└── .streamlit/
    └── config.toml
```

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Build the REBNY cache

Install scraper dependencies and run this once locally:

```bash
pip install -r requirements-scraper.txt
python -m playwright install chromium
python tools/download_rebny_members.py --output data/rebny_members.xlsx --deep
```

Then commit `data/rebny_members.xlsx` to the repo.

The app also lets you upload a REBNY cache XLSX/CSV from the sidebar. The cache can use any of these column names:

- `name`, `member name`, or `full name`
- optional `company`
- optional `title`
- optional `profile_url`

## Input spreadsheet

Required columns:

- `First Name`
- `Last Name`

Optional columns:

- `State`
- `Zip` or `Zip Code`

State and Zip improve FEC disambiguation.

## Streamlit Cloud

1. Upload this whole repo to GitHub.
2. In Streamlit Cloud, set the main file to `app.py`.
3. Optional: add `FEC_API_KEY` in Streamlit secrets.
4. Commit `data/rebny_members.xlsx`, or upload the cache through the app sidebar.

## Tests

```bash
pip install -r requirements-dev.txt
python run_tests.py
pytest
```
