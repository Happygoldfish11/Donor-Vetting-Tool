"""OpenFEC individual-contribution lookup.

The original app only checked the first page of FEC results and mixed true
party fields with broad keywords. This module paginates, filters returned donor
names conservatively, and separates true flags from review cases.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .models import FecDonation, FecLookupResult, Person
from .normalization import normalize_fec_contributor_name, normalize_name, similarity

FEC_SCHEDULE_A_URL = "https://api.open.fec.gov/v1/schedules/schedule_a/"
REPUBLICAN_PARTY_CODES = {"REP"}

# Keep this list intentionally conservative. Do not include generic issue terms
# such as "Israel" that can be bipartisan and cause false positives.
REPUBLICAN_COMMITTEE_KEYWORDS = {
    "republican", " rnc", "nrcc", "nrsc", "gop", "maga", "trump",
    "america first", "conservative", "right to rise", "club for growth",
    "freedomworks", "freedom works", "tea party", "heritage action",
    "citizens united", "american crossroads", "congressional leadership fund",
    "senate leadership fund", "save america", "make america great again",
    "republican national committee", "national republican", "house freedom fund",
}


def _committee_name(record: dict[str, Any]) -> str:
    committee = record.get("committee") or {}
    return str(committee.get("name") or record.get("committee_name") or "")


def _party(record: dict[str, Any]) -> str:
    committee = record.get("committee") or {}
    for key in ("party", "party_full", "committee_party", "candidate_party", "candidate_party_full"):
        value = committee.get(key) if key in committee else record.get(key)
        if value:
            return str(value).upper()
    return ""


def is_republican_recipient(committee_name: str, party: str = "") -> tuple[bool, str]:
    party_norm = (party or "").upper().strip()
    if party_norm in REPUBLICAN_PARTY_CODES or party_norm == "REPUBLICAN PARTY":
        return True, "party field is Republican"
    name = f" {normalize_name(committee_name)} "
    for keyword in sorted(REPUBLICAN_COMMITTEE_KEYWORDS, key=len, reverse=True):
        kw = f" {normalize_name(keyword)} "
        if kw.strip() and kw in name:
            return True, f"committee name matched keyword '{keyword.strip()}'"
    return False, "recipient not classified as Republican"


def donor_name_match_score(first_name: str, last_name: str, fec_contributor_name: str) -> int:
    query = normalize_name(f"{first_name} {last_name}")
    fec_name = normalize_fec_contributor_name(fec_contributor_name)
    if not query or not fec_name:
        return 0
    q_tokens = query.split()
    f_tokens = fec_name.split()
    if q_tokens[0] in f_tokens and q_tokens[-1] in f_tokens:
        return 100
    if q_tokens[-1] in f_tokens and any(tok == q_tokens[0][:1] for tok in f_tokens if len(tok) == 1):
        return 88
    return int(round(similarity(query, fec_name)))


def record_matches_person(record: dict[str, Any], person: Person, *, min_score: int = 96) -> tuple[bool, int]:
    contributor = str(record.get("contributor_name") or "")
    score = donor_name_match_score(person.first_name, person.last_name, contributor)
    if score < min_score:
        return False, score

    state = (person.state or "").upper().strip()
    if state:
        rec_state = str(record.get("contributor_state") or "").upper().strip()
        if rec_state and rec_state != state:
            return False, score

    zip_code = "".join(ch for ch in str(person.zip_code or "") if ch.isdigit())[:5]
    if zip_code:
        rec_zip = "".join(ch for ch in str(record.get("contributor_zip") or "") if ch.isdigit())[:5]
        if rec_zip and rec_zip != zip_code:
            return False, score

    return True, score


def _build_params(person: Person, api_key: str, cycles: list[int], page: int, per_page: int) -> dict[str, Any]:
    params: dict[str, Any] = {
        "api_key": api_key,
        "contributor_name": f"{person.last_name}, {person.first_name}".upper(),
        "per_page": per_page,
        "page": page,
        "sort": "-contribution_receipt_date",
    }
    if cycles:
        params["two_year_transaction_period"] = cycles
    if person.state:
        params["contributor_state"] = person.state.upper().strip()
    if person.zip_code:
        params["contributor_zip"] = "".join(ch for ch in str(person.zip_code) if ch.isdigit())[:5]
    return params


def lookup_donor(
    person: Person,
    api_key: str,
    *,
    cycles: list[int] | None = None,
    per_page: int = 100,
    max_pages: int = 50,
    session: Any | None = None,
    timeout: int = 15,
    false_positive_threshold: int = 25,
) -> FecLookupResult:
    """Look up a person in OpenFEC Schedule A and flag Republican recipients."""
    if not person.first_name or not person.last_name:
        return FecLookupResult(status="invalid input", flag=False, needs_review=True, detail="First and last name required.")
    if not api_key:
        api_key = "DEMO_KEY"
    cycles = cycles or [2026, 2024, 2022]

    if session is None:
        import requests  # lazy import for stdlib-only tests
        session = requests.Session()

    scanned = 0
    reported_count = 0
    republican: list[FecDonation] = []
    saw_low_confidence_matches = False
    pages_read = 0

    try:
        for page in range(1, max_pages + 1):
            pages_read = page
            params = _build_params(person, api_key, cycles, page, per_page)
            response = session.get(FEC_SCHEDULE_A_URL, params=params, timeout=timeout)
            if response.status_code == 429:
                return FecLookupResult(
                    status="rate_limited", flag=bool(republican), needs_review=True,
                    total_records_reported=reported_count, records_scanned=scanned,
                    republican_count=len(republican), republican_total=sum(d.amount for d in republican),
                    top_recipients=_top_recipients(republican), donations=republican,
                    detail="OpenFEC rate limit hit before all pages were scanned.",
                )
            if response.status_code != 200:
                return FecLookupResult(
                    status="error", flag=bool(republican), needs_review=True,
                    total_records_reported=reported_count, records_scanned=scanned,
                    republican_count=len(republican), republican_total=sum(d.amount for d in republican),
                    top_recipients=_top_recipients(republican), donations=republican,
                    detail=f"OpenFEC returned HTTP {response.status_code}.",
                )

            payload = response.json()
            pagination = payload.get("pagination") or {}
            reported_count = int(pagination.get("count") or reported_count or 0)
            results = payload.get("results") or []
            if not results:
                break

            for record in results:
                scanned += 1
                matched, score = record_matches_person(record, person)
                if not matched:
                    if score >= 86:
                        saw_low_confidence_matches = True
                    continue
                committee_name = _committee_name(record)
                party = _party(record)
                is_rep, _basis = is_republican_recipient(committee_name, party)
                if is_rep:
                    republican.append(
                        FecDonation(
                            contributor_name=str(record.get("contributor_name") or ""),
                            committee_name=committee_name,
                            amount=float(record.get("contribution_receipt_amount") or 0),
                            date=str(record.get("contribution_receipt_date") or ""),
                            party=party,
                            transaction_id=str(record.get("transaction_id") or ""),
                            donor_match_score=score,
                        )
                    )

            page_count = int(pagination.get("pages") or page)
            if page >= page_count:
                break

    except Exception as exc:
        return FecLookupResult(
            status="error", flag=bool(republican), needs_review=True,
            total_records_reported=reported_count, records_scanned=scanned,
            republican_count=len(republican), republican_total=sum(d.amount for d in republican),
            top_recipients=_top_recipients(republican), donations=republican,
            detail=f"OpenFEC lookup failed: {exc}",
        )

    truncated = reported_count > scanned and pages_read >= max_pages
    needs_review = truncated or reported_count >= false_positive_threshold or saw_low_confidence_matches
    rep_total = sum(d.amount for d in republican)
    detail_parts: list[str] = []
    if republican:
        detail_parts.append(f"${rep_total:,.0f} in Republican-classified contributions across {len(republican)} itemized record(s)")
    else:
        detail_parts.append("No Republican-classified itemized contributions found among matched donor records")
    if reported_count >= false_positive_threshold:
        detail_parts.append(f"common-name review: OpenFEC reported {reported_count} total name-search record(s)")
    if truncated:
        detail_parts.append(f"scan truncated at {scanned} records; increase max_pages for exhaustive scan")
    if saw_low_confidence_matches:
        detail_parts.append("some returned donor names were close but not exact first+last matches")

    return FecLookupResult(
        status="ok",
        flag=bool(republican),
        needs_review=needs_review,
        total_records_reported=reported_count,
        records_scanned=scanned,
        republican_count=len(republican),
        republican_total=rep_total,
        top_recipients=_top_recipients(republican),
        detail="; ".join(detail_parts),
        donations=republican,
    )


def _top_recipients(donations: list[FecDonation], limit: int = 3) -> str:
    if not donations:
        return ""
    totals: Counter[str] = Counter()
    for donation in donations:
        totals[donation.committee_name] += donation.amount
    return ", ".join(name for name, _amount in totals.most_common(limit))
