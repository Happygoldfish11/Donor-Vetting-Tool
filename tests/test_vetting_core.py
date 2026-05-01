import pandas as pd

from vetting_core import (
    Person,
    RebnyMember,
    find_column,
    is_republican_recipient,
    load_rebny_members,
    lookup_rebny_from_members,
    name_tokens,
    people_from_dataframe,
    score_rebny_member,
)


def test_people_from_dataframe_accepts_common_columns():
    df = pd.DataFrame(
        {
            "First Name": ["Jane"],
            "Last Name": ["Doe"],
            "State": ["NY"],
            "Zip Code": ["10001-1234"],
        }
    )
    people, mapping = people_from_dataframe(df)
    assert len(people) == 1
    assert people[0].first_name == "Jane"
    assert people[0].last_name == "Doe"
    assert people[0].zip_code == "10001"
    assert mapping["first"] == "First Name"


def test_name_tokens_remove_suffixes():
    assert name_tokens("John Q. Public Jr.") == ["john", "q", "public"]


def test_rebny_exact_match_found():
    person = Person("Jane", "Doe")
    member = RebnyMember(name="Jane A. Doe", company="Example Realty")
    result = lookup_rebny_from_members(person, [member])
    assert result.found is True
    assert result.result == "FOUND"
    assert result.match_name == "Jane A. Doe"


def test_rebny_initial_match_review():
    person = Person("Jane", "Doe")
    member = RebnyMember(name="J. Doe", company="Example Realty")
    result = lookup_rebny_from_members(person, [member])
    assert result.found is False
    assert result.review is True
    assert result.result == "REVIEW"


def test_rebny_not_found_clean():
    person = Person("Jane", "Doe")
    member = RebnyMember(name="Alice Smith", company="Example Realty")
    result = lookup_rebny_from_members(person, [member])
    assert result.found is False
    assert result.review is False
    assert result.result == "Clean"


def test_rebny_missing_cache_review():
    result = lookup_rebny_from_members(Person("Jane", "Doe"), [])
    assert result.review is True
    assert result.result == "REVIEW"


def test_republican_committee_detection():
    assert is_republican_recipient("Republican National Committee") is True
    assert is_republican_recipient("WinRed", "REP") is True
    assert is_republican_recipient("Friends of Public Schools", "DEM") is False


def test_load_rebny_members_from_file_like(tmp_path):
    path = tmp_path / "rebny_members.xlsx"
    pd.DataFrame({"name": ["Jane Doe"], "company": ["Example Realty"]}).to_excel(path, index=False)
    members = load_rebny_members(path)
    assert len(members) == 1
    assert members[0].display_name == "Jane Doe"


def test_find_column_fuzzy_alias():
    assert find_column(["Given Name", "Family Name"], ["first name", "given"]) == "Given Name"
