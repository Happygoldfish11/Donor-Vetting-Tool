"""Spreadsheet ingestion helpers."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .models import Person
from .normalization import parse_full_name

FIRST_ALIASES = {"first", "first name", "firstname", "given", "given name", "fname"}
LAST_ALIASES = {"last", "last name", "lastname", "surname", "family", "family name", "lname"}
FULL_ALIASES = {"name", "full name", "fullname", "donor", "member", "person"}
STATE_ALIASES = {"state", "st", "contributor state"}
ZIP_ALIASES = {"zip", "zipcode", "zip code", "postal", "postal code", "contributor zip"}
EMPLOYER_ALIASES = {"employer", "company", "firm", "organization"}
OCCUPATION_ALIASES = {"occupation", "title", "role", "job"}


def _canonical(col: str) -> str:
    return " ".join(str(col or "").strip().lower().replace("_", " ").replace("-", " ").split())


def detect_columns(columns: list[str]) -> dict[str, str]:
    normalized = {_canonical(c): c for c in columns}
    out: dict[str, str] = {}

    def choose(key: str, aliases: set[str]) -> None:
        for alias in aliases:
            if alias in normalized:
                out[key] = normalized[alias]
                return
        for norm, original in normalized.items():
            if any(alias in norm for alias in aliases):
                out[key] = original
                return

    choose("first", FIRST_ALIASES)
    choose("last", LAST_ALIASES)
    choose("full", FULL_ALIASES)
    choose("state", STATE_ALIASES)
    choose("zip", ZIP_ALIASES)
    choose("employer", EMPLOYER_ALIASES)
    choose("occupation", OCCUPATION_ALIASES)
    return out


def row_to_person(row: Any, columns: dict[str, str]) -> Person:
    def get(key: str) -> str:
        col = columns.get(key, "")
        if not col:
            return ""
        value = row.get(col, "") if hasattr(row, "get") else ""
        if value is None:
            return ""
        if isinstance(value, float) and str(value) == "nan":
            return ""
        return str(value).strip()

    first = get("first")
    last = get("last")
    if (not first or not last) and get("full"):
        parsed_first, parsed_last = parse_full_name(get("full"))
        first = first or parsed_first
        last = last or parsed_last
    return Person(
        first_name=first,
        last_name=last,
        state=get("state"),
        zip_code=get("zip"),
        employer=get("employer"),
        occupation=get("occupation"),
    )


def people_from_dataframe(df: Any) -> list[Person]:
    columns = detect_columns(list(df.columns))
    if ("first" not in columns or "last" not in columns) and "full" not in columns:
        raise ValueError("Could not find First Name + Last Name columns or a Full Name column.")
    people: list[Person] = []
    for _, row in df.iterrows():
        person = row_to_person(row, columns)
        if person.first_name or person.last_name:
            people.append(person)
    return people


def people_preview_rows(people: list[Person], limit: int = 5) -> list[dict[str, str]]:
    return [asdict(person) for person in people[:limit]]
