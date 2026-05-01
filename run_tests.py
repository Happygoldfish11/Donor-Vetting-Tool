"""Dependency-light offline test runner.

This exists so the core matching logic can be tested even in a minimal Python
environment. It imports no Streamlit, pandas, requests, bs4, or openpyxl.
"""
from __future__ import annotations

from donor_vetting.fec import is_republican_recipient, donor_name_match_score, record_matches_person, lookup_donor
from donor_vetting.models import Person, RebnyCandidate
from donor_vetting.normalization import normalize_name, normalize_fec_contributor_name, parse_full_name, first_last_match
from donor_vetting.rebny import classify_rebny_match, extract_member_candidates, score_rebny_candidate


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = 0
    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self.calls == 1:
            return FakeResponse({
                "pagination": {"count": 2, "pages": 2},
                "results": [{
                    "contributor_name": "SMITH, JOHN",
                    "contribution_receipt_amount": 250,
                    "contribution_receipt_date": "2024-01-01",
                    "committee": {"name": "Republican National Committee", "party": "REP"},
                    "transaction_id": "A1",
                }]
            })
        return FakeResponse({
            "pagination": {"count": 2, "pages": 2},
            "results": [{
                "contributor_name": "SMITH, JOHN",
                "contribution_receipt_amount": 100,
                "contribution_receipt_date": "2024-02-01",
                "committee": {"name": "Neutral Civic Committee", "party": "DEM"},
                "transaction_id": "A2",
            }]
        })


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS: {label}")


def main():
    check("normalize removes accents/suffixes", normalize_name("Dr. José O'Connor, Jr.") == "jose oconnor")
    check("parse Last, First", parse_full_name("Smith, John A.") == ("john", "smith"))
    check("normalize FEC contributor", normalize_fec_contributor_name("SMITH, JOHN A") == "john a smith")
    check("first+last with middle matches", first_last_match("John", "Smith", "John A. Smith")[0] is True)

    card_html = """
      <article class='member-card'><h3>Jane Q. Doe</h3><p>Example Realty - Residential - Broker</p></article>
    """
    candidates = extract_member_candidates(card_html)
    check("REBNY extracts card candidate", len(candidates) == 1 and candidates[0].name == "Jane Q. Doe")

    count_only = "<p>1 Members</p><h1>No Search Results Found</h1>"
    result, _ = classify_rebny_match("John", "Smith", extract_member_candidates(count_only))
    check("count-only REBNY response is not a match", result.match is False and result.status == "not found")

    result, _ = classify_rebny_match("Jane", "Doe", [RebnyCandidate(name="Jane Q. Doe", company="Example Realty")])
    check("REBNY exact first+last returns FOUND", result.match is True and result.status == "FOUND")

    score, reason = score_rebny_candidate("Jane", "Doe", RebnyCandidate(name="J. Doe"))
    result, _ = classify_rebny_match("Jane", "Doe", [RebnyCandidate(name="J. Doe")])
    check("REBNY initial-only goes to review", score >= 86 and "initial" in reason and result.needs_review is True and result.match is False)

    ok, _ = is_republican_recipient("Republican National Committee", "")
    check("FEC Republican committee keyword flags", ok is True)
    ok, _ = is_republican_recipient("Citizens for Israel and Democracy", "")
    check("generic Israel keyword does not flag", ok is False)
    check("FEC donor LAST, FIRST exact score", donor_name_match_score("John", "Smith", "SMITH, JOHN A") == 100)
    person = Person("John", "Smith", state="NY", zip_code="10001")
    record = {"contributor_name": "SMITH, JOHN", "contributor_state": "NY", "contributor_zip": "10001-1234"}
    check("FEC state/zip disambiguation passes", record_matches_person(record, person)[0] is True)
    record["contributor_state"] = "CA"
    check("FEC state disambiguation blocks mismatch", record_matches_person(record, person)[0] is False)

    fec_result = lookup_donor(Person("John", "Smith"), "DEMO_KEY", session=FakeSession(), max_pages=5)
    check("FEC lookup paginates", fec_result.records_scanned == 2)
    check("FEC flags only Republican-classified donations", fec_result.flag is True and fec_result.republican_count == 1 and fec_result.republican_total == 250)
    print("\nAll offline tests passed.")


if __name__ == "__main__":
    main()
