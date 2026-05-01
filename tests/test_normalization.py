from donor_vetting.normalization import normalize_name, normalize_fec_contributor_name, parse_full_name, first_last_match


def test_normalize_removes_suffix_and_punctuation():
    assert normalize_name("Dr. José O'Connor, Jr.") == "jose oconnor"


def test_parse_last_comma_first():
    assert parse_full_name("Smith, John A.") == ("john", "smith")


def test_fec_contributor_format_normalizes_to_first_last():
    assert normalize_fec_contributor_name("SMITH, JOHN A") == "john a smith"


def test_first_last_match_allows_middle_name():
    ok, reason = first_last_match("John", "Smith", "John Allen Smith")
    assert ok is True
    assert "first and last" in reason
