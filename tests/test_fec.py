from donor_vetting.fec import is_republican_recipient, donor_name_match_score, record_matches_person, lookup_donor
from donor_vetting.models import Person


def test_party_field_republican_flags():
    ok, reason = is_republican_recipient("Some Committee", "REP")
    assert ok is True
    assert "party" in reason


def test_generic_israel_keyword_does_not_flag():
    ok, _ = is_republican_recipient("Citizens for Israel and Democracy", "")
    assert ok is False


def test_fec_donor_name_match_score_for_last_comma_first():
    assert donor_name_match_score("John", "Smith", "SMITH, JOHN A") == 100


def test_record_matches_person_uses_state_and_zip():
    person = Person("John", "Smith", state="NY", zip_code="10001")
    record = {"contributor_name": "SMITH, JOHN", "contributor_state": "NY", "contributor_zip": "10001-1234"}
    assert record_matches_person(record, person)[0] is True
    record["contributor_state"] = "CA"
    assert record_matches_person(record, person)[0] is False


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
                "results": [
                    {
                        "contributor_name": "SMITH, JOHN",
                        "contribution_receipt_amount": 250,
                        "contribution_receipt_date": "2024-01-01",
                        "committee": {"name": "Republican National Committee", "party": "REP"},
                        "transaction_id": "A1",
                    }
                ]
            })
        return FakeResponse({
            "pagination": {"count": 2, "pages": 2},
            "results": [
                {
                    "contributor_name": "SMITH, JOHN",
                    "contribution_receipt_amount": 100,
                    "contribution_receipt_date": "2024-02-01",
                    "committee": {"name": "Neutral Civic Committee", "party": "DEM"},
                    "transaction_id": "A2",
                }
            ]
        })


def test_lookup_donor_paginates_and_flags_republican_only():
    result = lookup_donor(Person("John", "Smith"), "DEMO_KEY", session=FakeSession(), max_pages=5)
    assert result.status == "ok"
    assert result.records_scanned == 2
    assert result.flag is True
    assert result.republican_count == 1
    assert result.republican_total == 250
