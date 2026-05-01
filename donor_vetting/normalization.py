"""Name normalization and matching helpers used by both REBNY and FEC checks.

The goal is conservative matching: exact first+last matches should pass, obvious
middle-name / punctuation variants should pass, and ambiguous initials should be
sent to review rather than counted as a true hit.
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Iterable

HONORIFICS = {
    "mr", "mrs", "ms", "miss", "mx", "dr", "prof", "hon", "judge",
    "sen", "rep", "assemblymember", "councilmember",
}

SUFFIXES = {
    "jr", "sr", "ii", "iii", "iv", "v", "esq", "md", "phd", "cpa", "nyrs",
}

COMPANY_WORDS = {
    "llc", "inc", "corp", "corporation", "company", "co", "ltd", "lp", "llp",
    "group", "partners", "properties", "property", "realty", "management",
    "services", "estate", "estates", "brokerage", "capital", "advisors",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def strip_accents(value: str) -> str:
    """Convert accented unicode characters to their ASCII equivalents."""
    decomposed = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(value: str) -> str:
    """Return a lowercase, punctuation-light representation of a person name."""
    value = strip_accents(value or "")
    value = value.replace("&", " and ")
    value = re.sub(r"['’`´]", "", value)
    value = re.sub(r"[^A-Za-z0-9]+", " ", value)
    tokens = []
    for token in value.lower().split():
        if token in HONORIFICS or token in SUFFIXES:
            continue
        tokens.append(token)
    return " ".join(tokens).strip()


def name_tokens(value: str) -> list[str]:
    return _TOKEN_RE.findall(normalize_name(value))


def compact_name(value: str) -> str:
    return "".join(name_tokens(value))


def token_sort_name(value: str) -> str:
    return " ".join(sorted(name_tokens(value)))


def similarity(a: str, b: str) -> float:
    """Return a 0-100 similarity score using stdlib only."""
    a_norm = normalize_name(a)
    b_norm = normalize_name(b)
    if not a_norm or not b_norm:
        return 0.0
    direct = SequenceMatcher(None, a_norm, b_norm).ratio()
    sorted_ratio = SequenceMatcher(None, token_sort_name(a_norm), token_sort_name(b_norm)).ratio()
    compact_ratio = SequenceMatcher(None, compact_name(a_norm), compact_name(b_norm)).ratio()
    return round(max(direct, sorted_ratio, compact_ratio) * 100, 2)


def parse_full_name(value: str) -> tuple[str, str]:
    """Split a full name into best-effort first and last names.

    Handles both "First Middle Last" and "Last, First Middle".
    """
    raw = (value or "").strip()
    if not raw:
        return "", ""
    if "," in raw:
        last, rest = raw.split(",", 1)
        first_tokens = name_tokens(rest)
        last_tokens = name_tokens(last)
        return (first_tokens[0] if first_tokens else "", last_tokens[-1] if last_tokens else "")
    tokens = name_tokens(raw)
    if len(tokens) == 1:
        return tokens[0], ""
    return tokens[0], tokens[-1]


def normalize_fec_contributor_name(value: str) -> str:
    """Normalize FEC contributor formats like 'LAST, FIRST MIDDLE'."""
    value = value or ""
    if "," in value:
        last, rest = value.split(",", 1)
        return normalize_name(f"{rest.strip()} {last.strip()}")
    return normalize_name(value)


def first_last_match(query_first: str, query_last: str, candidate_name: str) -> tuple[bool, str]:
    """Conservatively decide if a candidate name is the same first+last person."""
    q_first = normalize_name(query_first)
    q_last = normalize_name(query_last)
    c_tokens = name_tokens(candidate_name)
    if not q_first or not q_last or not c_tokens:
        return False, "missing first or last name"

    if q_first in c_tokens and q_last in c_tokens:
        return True, "first and last tokens both matched"

    # Support names with initials, but treat them as review-grade in scoring.
    first_initial_ok = any(tok == q_first[:1] for tok in c_tokens if len(tok) == 1)
    last_ok = q_last in c_tokens
    if first_initial_ok and last_ok:
        return False, "last name matched, first initial only"

    return False, "first+last tokens did not both match"


def looks_like_person_line(line: str) -> bool:
    """Heuristic for extracting person names from public directory HTML text."""
    raw = (line or "").strip()
    if not raw or len(raw) > 80:
        return False
    lowered = normalize_name(raw)
    if not lowered:
        return False
    tokens = lowered.split()
    if not (2 <= len(tokens) <= 6):
        return False
    if any(tok in COMPANY_WORDS for tok in tokens):
        return False
    noisy = {
        "member", "members", "directory", "search", "filter", "sort", "login",
        "contact", "privacy", "terms", "resources", "education", "advocacy",
        "residential", "commercial", "brokerage", "upcoming", "events", "image",
        "rebny", "button", "btn", "found", "results",
    }
    if any(tok in noisy for tok in tokens):
        return False
    # Require at least two alphabetic-looking tokens; this avoids counts/buttons.
    return sum(1 for tok in tokens if tok.isalpha()) >= 2


def dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = normalize_name(value)
        if key and key not in seen:
            seen.add(key)
            out.append(value)
    return out
