import sys
import pandas as pd

from app import REBNYDirectoryClient


def find_name_columns(df):
    cols = {c.lower().strip(): c for c in df.columns}

    first_col = None
    last_col = None

    for c in df.columns:
        low = c.lower().strip()
        if "first" in low and first_col is None:
            first_col = c
        if "last" in low and last_col is None:
            last_col = c

    if not first_col or not last_col:
        raise ValueError("Could not find First Name and Last Name columns.")

    return first_col, last_col


def main():
    if len(sys.argv) < 3:
        print("Usage: python run_rebny_csv.py input.csv output.xlsx")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    if input_path.lower().endswith(".csv"):
        df = pd.read_csv(input_path)
    else:
        df = pd.read_excel(input_path)

    first_col, last_col = find_name_columns(df)

    results = []

    with REBNYDirectoryClient(headless=True) as client:
        for i, row in df.iterrows():
            first = str(row[first_col]).strip()
            last = str(row[last_col]).strip()

            if not first or not last or first.lower() == "nan" or last.lower() == "nan":
                result = {
                    "rebny_status": "not found",
                    "rebny_match": False,
                    "rebny_result_count": "",
                    "rebny_detail": "Blank name",
                }
            else:
                print(f"Checking {i + 1}/{len(df)}: {first} {last}")
                result = client.lookup(first, last)

            results.append(result)

    out = df.copy()
    out["REBNY Member?"] = [
        "YES" if r.get("rebny_match") else r.get("rebny_status", "unknown")
        for r in results
    ]
    out["REBNY Result Count"] = [
        r.get("rebny_result_count", "")
        for r in results
    ]
    out["REBNY Detail"] = [
        r.get("rebny_detail", "")
        for r in results
    ]

    out.to_excel(output_path, index=False)
    print(f"Done. Saved to {output_path}")


if __name__ == "__main__":
    main()
