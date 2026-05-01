"""Typed result objects for the donor vetting tool."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Person:
    first_name: str
    last_name: str
    state: str = ""
    zip_code: str = ""
    employer: str = ""
    occupation: str = ""

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


@dataclass(slots=True)
class RebnyCandidate:
    name: str
    company: str = ""
    category: str = ""
    role: str = ""
    source_url: str = ""
    raw_text: str = ""


@dataclass(slots=True)
class RebnyLookupResult:
    status: str
    match: bool
    needs_review: bool
    confidence: int
    matched_name: str = ""
    company: str = ""
    category: str = ""
    role: str = ""
    result_count: int = 0
    detail: str = ""
    source_url: str = ""
    candidates: list[RebnyCandidate] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        return {
            "REBNY Status": self.status,
            "REBNY Match?": "YES" if self.match else "REVIEW" if self.needs_review else "NO",
            "REBNY Confidence": self.confidence,
            "REBNY Matched Name": self.matched_name,
            "REBNY Company": self.company,
            "REBNY Category": self.category,
            "REBNY Role": self.role,
            "REBNY Result Count": self.result_count,
            "REBNY Detail": self.detail,
            "REBNY Source": self.source_url,
        }


@dataclass(slots=True)
class FecDonation:
    contributor_name: str
    committee_name: str
    amount: float
    date: str
    party: str = ""
    transaction_id: str = ""
    donor_match_score: int = 0


@dataclass(slots=True)
class FecLookupResult:
    status: str
    flag: bool
    needs_review: bool
    total_records_reported: int = 0
    records_scanned: int = 0
    republican_count: int = 0
    republican_total: float = 0.0
    top_recipients: str = ""
    detail: str = ""
    donations: list[FecDonation] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        if self.flag:
            status = "FLAGGED — REVIEW" if self.needs_review else "FLAGGED"
        else:
            status = "REVIEW NEEDED" if self.needs_review else "Clean"
        return {
            "FEC Status": status,
            "GOP Donations ($)": f"${self.republican_total:,.0f}" if self.flag else "—",
            "GOP Donation Count": self.republican_count,
            "Top GOP Recipients": self.top_recipients,
            "FEC Records Reported": self.total_records_reported,
            "FEC Records Scanned": self.records_scanned,
            "FEC Detail": self.detail,
        }
