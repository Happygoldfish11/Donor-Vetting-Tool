from __future__ import annotations

import argparse
from pathlib import Path
from vetting_core import Person, RebnyCache


def main():
    p = argparse.ArgumentParser()
    p.add_argument("name", nargs="+", help="Name to check, e.g. Jane Doe")
    p.add_argument("--cache", default="data/rebny_members.xlsx")
    args = p.parse_args()
    name = " ".join(args.name).strip()
    parts = name.split()
    if len(parts) < 2:
        raise SystemExit("Use first and last name.")
    cache = RebnyCache.from_file(Path(args.cache))
    result = cache.match_person(Person(parts[0], parts[-1]))
    print(result.as_row())

if __name__ == "__main__":
    main()
