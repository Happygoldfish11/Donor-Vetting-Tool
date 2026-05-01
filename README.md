# Donor Vetting Tool — REBNY + OpenFEC

This is a corrected, production-style Streamlit app for batch vetting names against:

1. **OpenFEC Schedule A itemized contributions** for Republican-classified recipients.
2. **REBNY's public Member Directory** for real-estate-board membership.

The important fix: **REBNY results are no longer based on a member-count string.** The app extracts returned member candidates and only marks `FOUND` when the returned candidate name itself matches the searched first and last name.

---

## Why the original app was unreliable

The old REBNY logic treated any parsed `N Members` count greater than zero as a confirmed match. That creates false positives when the public directory page returns a generic count, stale shell content, unrelated cards, or a partially-rendered result. The new logic is conservative:

- Fetch the public REBNY directory search page for the person.
- Extract actual member card/name candidates from HTML/JSON/text.
- Score each candidate against the searched first and last name.
- Return:
  - `FOUND` only for strong first+last matches.
  - `review` for close/ambiguous matches.
  - `not found` for no actual candidate match.

---

## Files

```text
app.py                         Streamlit UI
requirements.txt               Python dependencies
.streamlit/config.toml          Dark theme config
donor_vetting/
  batch.py                      Spreadsheet column detection + row parsing
  excel.py                      Colored Excel export
  fec.py                        OpenFEC lookup/classification
  models.py                     Typed result objects
  normalization.py              Name normalization + similarity helpers
  rebny.py                      REBNY directory fetch/parse/match logic
tests/
  test_normalization.py
  test_rebny.py
  test_fec.py
run_tests.py                    Dependency-light offline test runner
```

---

## Install

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Optional, but recommended for JS-rendered fallback:

```bash
playwright install chromium
```

The app first tries normal HTTP. If the REBNY page returns only a shell and Playwright is installed, it falls back to headless Chromium rendering.

---

## Run

```bash
streamlit run app.py
```

Upload a `.csv` or `.xlsx` with either:

- `First Name` and `Last Name`, or
- `Full Name`

Optional disambiguation columns:

- `State`
- `Zip`
- `Employer`
- `Occupation`

State and Zip are passed into the FEC lookup when present, and then records are still locally re-filtered by exact donor name.

---

## OpenFEC key

The app works with `DEMO_KEY`, but a real key is strongly recommended for batch runs:

```text
https://api.open.fec.gov/developers/
```

Paste the key in the sidebar. It is not saved.

---

## REBNY accuracy policy

The REBNY checker uses the public member directory page. It does **not** claim access to the private REBNY RLS or any private member database.

A REBNY result means:

- `FOUND`: the app found a returned public-directory candidate whose first and last name match the searched person.
- `review`: the directory returned a close but ambiguous candidate, such as an initial-only match.
- `not found`: no returned candidate matched the searched first+last name.
- `error`: the public directory could not be reached or parsed.

The app intentionally avoids bulk-copying REBNY's directory. It performs live lookup-by-name checks with throttling and caching.

---

## Tests

Run the dependency-light test runner:

```bash
python run_tests.py
```

Or with pytest:

```bash
pytest -q
```

The included tests cover:

- name normalization,
- FEC donor-name matching,
- Republican recipient classification,
- REBNY candidate extraction,
- REBNY exact match / review / not-found logic,
- the specific count-based false-positive bug.

---

## Notes for deployment

If deploying to Streamlit Community Cloud or another hosted environment, include:

```bash
playwright install chromium
```

in your build/startup steps if you want rendered-page fallback. If you do not install Playwright, the HTTP parser still works when REBNY returns server-rendered content, but JS-only responses may return `error`/`not found` instead of rendered results.
