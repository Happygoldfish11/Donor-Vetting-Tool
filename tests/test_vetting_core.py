import pandas as pd

from vetting_core import Person, RebnyCache, normalize_name, people_from_dataframe, is_republican_recipient


def test_normalize_name():
    assert normalize_name(" Jane A. Doe, Jr. ") == "jane a doe"


def test_people_from_dataframe():
    df = pd.DataFrame({"First Name": ["Jane"], "Last Name": ["Doe"], "State": ["NY"]})
    people = people_from_dataframe(df)
    assert people == [Person("Jane", "Doe", "NY", "")]


def test_rebny_exact_match():
    cache = RebnyCache([{"name": "Jane Doe", "company": "Example Realty", "category": "Residential - Brokerage"}])
    m = cache.match_person(Person("Jane", "Doe"))
    assert m.matched is True
    assert m.status == "FOUND"
    assert m.review is False


def test_rebny_initial_review():
    cache = RebnyCache([{"name": "J. Doe", "company": "Example Realty"}])
    m = cache.match_person(Person("Jane", "Doe"))
    assert m.status == "REVIEW"
    assert m.review is True


def test_rebny_not_found():
    cache = RebnyCache([{"name": "Other Person"}])
    m = cache.match_person(Person("Jane", "Doe"))
    assert m.status == "not found"


def test_republican_recipient():
    assert is_republican_recipient("National Republican Senatorial Committee", "")
    assert is_republican_recipient("Some Committee", "REP")
    assert not is_republican_recipient("Generic Israel Education Fund", "")
