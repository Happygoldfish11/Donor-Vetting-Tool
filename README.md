# Donor Vetting

Streamlit app for checking a donor spreadsheet against a local REBNY member cache and optional OpenFEC records.

## Run the app

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Build the REBNY cache

```bash
pip install -r requirements-scraper.txt
python -m playwright install chromium
python tools/download_rebny_members.py --output data/rebny_members.xlsx --deep
```

Commit `data/rebny_members.xlsx` before deploying, or upload it in the app sidebar.

## Check one name from the cache

```bash
python tools/quick_rebny_lookup.py "Jane Doe" --cache data/rebny_members.xlsx
```

## Tests

```bash
pip install -r requirements-dev.txt
python run_tests.py
```
