from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
import io
import re
import unicodedata

import pandas as pd
import requests

NAME_SUFFIXES = {
    "jr", "sr", "ii", "iii", "iv", "v", "esq", "esquire", "cpa", "pe", "phd", "md"
}

REPUBLICAN_PARTY_CODES = {"REP"}
REPUBLICAN_COMMITTEE_TERMS = {
    "republican", "rnc", "nrcc", "nrsc", "gop", "maga", "trump",
    "america first", "conservative", "right to rise", "tea party",
    "club for growth", "freedomworks", "heritage action", "citizens united",
    "american crossroads", "congressional leadership fund", "senate leadership fund",
}

@dataclass(frozen=True)
class Person:
    first_name: str
    last_name: str
    state: str = ""
    zip_code: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

@dataclass(frozen=True)
class RebnyMatch:
    status: str
    review: bool
    matched: bool
    matched_name: str = ""
    company: str = ""
    category: str = ""
    score: float = 0.0
    detail: str = ""

    def as_row(self) -> dict:
        return {
            "REBNY Status": self.status,
            "REBNY Review": "YES" if self.review else "",
            "REBNY Matched Name": self.matched_name,
            "REBNY Company": self.company,
            "REBNY Category": self.category,
            "REBNY Score": round(self.score, 3),
            "REBNY Detail": self.detail,
        }

@dataclass(frozen=True)
class FecMatch:
    status: str
    review: bool
    flagged: bool
    total_records: int = 0
    republican_records: int = 0
    republican_total: float = 0.0
    recipients: str = ""
    detail: str = ""

    def as_row(self) -> dict:
        label = "FLAGGED" if self.flagged else "CLEAR"
        if self.review and self.flagged:
            label = "FLAGGED - REVIEW"
        elif self.review:
            label = "REVIEW"
        if self.status != "ok":
            label = self.status.upper()
        return {
            "FEC Status": label,
            "FEC Review": "YES" if self.review else "",
            "GOP Donations ($)": self.republican_total if self.flagged else 0,
            "GOP Donation Count": self.republican_records,
            "FEC Records Found": self.total_records,
            "FEC Recipients": self.recipients,
            "FEC Detail": self.detail,
        }

class RebnyCache:
    def __init__(self, records: Iterable[dict]):
        self.records: list[dict] = []
        for raw in records:
            name = clean_cell(raw.get("name") or raw.get("full_name") or raw.get("member_name") or "")
            first = clean_cell(raw.get("first_name") or "")
            last = clean_cell(raw.get("last_name") or "")
            if not name and (first or last):
                name = f"{first} {last}".strip()
            if not name:
                continue
            first2, last2 = split_name(name)
            if not first:
                first = first2
            if not last:
                last = last2
            row = {
                "name": name,
                "first_name": first,
                "last_name": last,
                "company": clean_cell(raw.get("company") or raw.get("organization") or raw.get("firm") or ""),
                "category": clean_cell(raw.get("category") or raw.get("member_type") or raw.get("membership_type") or raw.get("division") or ""),
                "source_query": clean_cell(raw.get("source_query") or ""),
                "source_url": clean_cell(raw.get("source_url") or ""),
                "raw_text": clean_cell(raw.get("raw_text") or ""),
            }
            row["norm_name"] = normalize_name(name)
            row["norm_first"] = normalize_name(first)
            row["norm_last"] = normalize_name(last)
            self.records.append(row)

    @classmethod
    def from_file(cls, file_or_path) -> "RebnyCache":
        df = read_table(file_or_path)
        df = normalize_columns(df)
        if "name" not in df.columns and not ({"first_name", "last_name"} <= set(df.columns)):
            name_col = guess_column(df.columns, ["member", "full_name", "full name", "name"])
            if name_col:
                df = df.rename(columns={name_col: "name"})
        mappings = {
            "first": "first_name", "firstname": "first_name", "first_name": "first_name", "first name": "first_name",
            "last": "last_name", "lastname": "last_name", "last_name": "last_name", "last name": "last_name",
            "member": "name", "member_name": "name", "member name": "name", "full name": "name", "full_name": "name",
            "organization": "company", "firm": "company", "company name": "company", "company_name": "company",
            "member type": "category", "membership type": "category", "division": "category",
        }
        df = df.rename(columns={c: mappings[c] for c in df.columns if c in mappings})
        return cls(df.fillna("").to_dict(orient="records"))

    def match_person(self, person: Person) -> RebnyMatch:
        target_first = normalize_name(person.first_name)
        target_last = normalize_name(person.last_name)
        target_full = normalize_name(person.full_name)
        if not target_first or not target_last:
            return RebnyMatch("error", True, False, detail="missing first or last name")
        best: Optional[dict] = None
        best_score = 0.0
        best_reason = ""
        for r in self.records:
            score, reason = score_name_match(target_first, target_last, target_full, r)
            if score > best_score:
                best_score, best, best_reason = score, r, reason
        if not best:
            return RebnyMatch("not found", False, False, detail="no cache record matched")
        if best_score >= 1.0:
            return RebnyMatch("FOUND", False, True, best["name"], best["company"], best["category"], best_score, best_reason)
        if best_score >= 0.78:
            return RebnyMatch("REVIEW", True, False, best["name"], best["company"], best["category"], best_score, best_reason)
        return RebnyMatch("not found", False, False, best["name"], best["company"], best["category"], best_score, "below match threshold")

    def to_dataframe(self) -> pd.DataFrame:
        cols = ["name", "first_name", "last_name", "company", "category", "source_query", "source_url", "raw_text"]
        return pd.DataFrame([{c: r.get(c, "") for c in cols} for r in self.records])


def clean_cell(value) -> str:
    if value is None:
        return ""
    value = str(value).replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [re.sub(r"\s+", "_", str(c).strip().lower()) for c in out.columns]
    return out


def guess_column(columns, needles) -> str:
    for n in needles:
        n2 = n.replace(" ", "_")
        for c in columns:
            if n2 == c or n2 in c:
                return c
    return ""


def read_table(file_or_path) -> pd.DataFrame:
    name = getattr(file_or_path, "name", str(file_or_path)).lower()
    if isinstance(file_or_path, (str, Path)):
        path = Path(file_or_path)
        if path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
            return pd.read_excel(path)
        return pd.read_csv(path)
    data = file_or_path.getvalue() if hasattr(file_or_path, "getvalue") else file_or_path.read()
    bio = io.BytesIO(data)
    if name.endswith((".xlsx", ".xlsm", ".xls")):
        return pd.read_excel(bio)
    return pd.read_csv(bio)


def normalize_name(value: str) -> str:
    value = clean_cell(value).lower()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9\s'-]", " ", value)
    value = value.replace("'", "")
    value = re.sub(r"\s+", " ", value).strip()
    tokens = [t for t in value.split() if t not in NAME_SUFFIXES]
    return " ".join(tokens)


def split_name(full_name: str) -> tuple[str, str]:
    n = clean_cell(full_name)
    if not n:
        return "", ""
    if "," in n:
        left, right = [x.strip() for x in n.split(",", 1)]
        first = right.split()[0] if right else ""
        last = left.split()[0] if left else ""
        return first, last
    parts = n.split()
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def score_name_match(target_first: str, target_last: str, target_full: str, record: dict) -> tuple[float, str]:
    rn = record.get("norm_name", "")
    rf = record.get("norm_first", "")
    rl = record.get("norm_last", "")
    if rn == target_full:
        return 1.0, "exact full-name match"
    if rf == target_first and rl == target_last:
        return 1.0, "exact first/last match"
    rtokens = set(rn.split())
    ttokens = set(target_full.split())
    if target_first in rtokens and target_last in rtokens:
        return 0.96, "first and last tokens present"
    if rl == target_last and rf and target_first and rf[0] == target_first[0]:
        return 0.84, "same last name and first initial"
    if target_last in rtokens and target_first and any(t.startswith(target_first[:3]) for t in rtokens if len(target_first) >= 3):
        return 0.82, "same last name and partial first name"
    overlap = len(ttokens & rtokens) / max(1, len(ttokens | rtokens))
    return overlap, "token overlap"


def people_from_dataframe(df: pd.DataFrame) -> list[Person]:
    df = normalize_columns(df)
    first_col = guess_column(df.columns, ["first_name", "first", "firstname"])
    last_col = guess_column(df.columns, ["last_name", "last", "lastname"])
    state_col = guess_column(df.columns, ["state", "st"])
    zip_col = guess_column(df.columns, ["zip_code", "zipcode", "zip", "postal"])
    if not first_col or not last_col:
        raise ValueError("Spreadsheet needs First Name and Last Name columns.")
    people = []
    for _, row in df.iterrows():
        first = clean_cell(row.get(first_col, ""))
        last = clean_cell(row.get(last_col, ""))
        if not first and not last:
            continue
        people.append(Person(
            first_name=first,
            last_name=last,
            state=clean_cell(row.get(state_col, "")) if state_col else "",
            zip_code=clean_cell(row.get(zip_col, "")) if zip_col else "",
        ))
    return people


def is_republican_recipient(committee_name: str, party: str) -> bool:
    if party and party.upper() in REPUBLICAN_PARTY_CODES:
        return True
    name = normalize_name(committee_name)
    return any(term in name for term in REPUBLICAN_COMMITTEE_TERMS)


def lookup_fec(person: Person, api_key: str, cycles=(2026, 2024, 2022, 2020), max_pages: int = 3) -> FecMatch:
    if not api_key:
        return FecMatch("skipped", False, False, detail="no FEC API key provided")
    name_query = f"{person.last_name}, {person.first_name}".upper()
    url = "https://api.open.fec.gov/v1/schedules/schedule_a/"
    results = []
    page = 1
    try:
        while page <= max_pages:
            params = {
                "api_key": api_key,
                "contributor_name": name_query,
                "two_year_transaction_period": list(cycles),
                "per_page": 100,
                "page": page,
                "sort": "-contribution_receipt_date",
            }
            if person.state:
                params["contributor_state"] = person.state.upper()
            resp = requests.get(url, params=params, timeout=20)
            if resp.status_code == 429:
                return FecMatch("rate_limited", True, False, detail="FEC API rate limit")
            if resp.status_code != 200:
                return FecMatch("error", True, False, detail=f"FEC HTTP {resp.status_code}")
            payload = resp.json()
            batch = payload.get("results", [])
            results.extend(batch)
            pages = int(payload.get("pagination", {}).get("pages") or page)
            if page >= pages:
                break
            page += 1
    except Exception as exc:
        return FecMatch("error", True, False, detail=str(exc))

    target = normalize_name(person.full_name)
    target_last = normalize_name(person.last_name)
    target_first = normalize_name(person.first_name)
    rep = []
    for r in results:
        donor_name = normalize_name(r.get("contributor_name", ""))
        if target_last not in donor_name or target_first not in donor_name:
            continue
        committee = r.get("committee") or {}
        committee_name = committee.get("name") or r.get("committee_name") or ""
        party = committee.get("party") or r.get("committee_party") or ""
        if is_republican_recipient(committee_name, party):
            try:
                amount = float(r.get("contribution_receipt_amount") or 0)
            except Exception:
                amount = 0.0
            rep.append((committee_name, amount))
    total = len(results)
    rep_total = sum(x[1] for x in rep)
    recipients = []
    for committee, _ in sorted(rep, key=lambda x: x[1], reverse=True):
        if committee and committee not in recipients:
            recipients.append(committee)
    review = total >= 25
    if rep:
        detail = f"{len(rep)} Republican/PAC record(s), ${rep_total:,.0f} total"
    elif review:
        detail = f"{total} FEC records; common name, review manually"
    else:
        detail = "no Republican donations found"
    return FecMatch("ok", review, bool(rep), total, len(rep), rep_total, "; ".join(recipients[:5]), detail)


def dataframe_to_excel_bytes(df: pd.DataFrame) -> bytes:
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Vetted")
    out.seek(0)
    return out.getvalue()
