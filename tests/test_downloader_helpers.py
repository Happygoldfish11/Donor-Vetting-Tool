from tools.download_rebny_members import extract_from_json, looks_like_person_name, member_from_text


def test_looks_like_person_name():
    assert looks_like_person_name("Jane Doe") is True
    assert looks_like_person_name("No Search Results Found") is False
    assert looks_like_person_name("Member Directory") is False


def test_member_from_text_extracts_name_and_company():
    member = member_from_text("Jane Doe\nExample Realty\nBroker", source_query="do")
    assert member is not None
    assert member.name == "Jane Doe"
    assert member.company == "Example Realty"


def test_extract_from_json_nested():
    payload = {"data": {"members": [{"name": "Jane Doe", "company": "Example Realty"}]}}
    members = extract_from_json(payload)
    assert len(members) == 1
    assert members[0].name == "Jane Doe"
