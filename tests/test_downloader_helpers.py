from tools.download_rebny_members import parse_text_block, extract_records_from_json, unique


def test_parse_text_block_person():
    text = """
    Member Directory
    A.J. Rexhepi
    Century Management Services, Inc. - Residential - Management
    No Search Results Found
    """
    records = parse_text_block(text, "aj")
    assert any(r.name == "A.J. Rexhepi" for r in records)
    rec = [r for r in records if r.name == "A.J. Rexhepi"][0]
    assert "Century" in rec.company


def test_parse_text_block_org():
    text = """
    Page 1 Properties
    Residential - Brokerage
    Palette
    Residential - Brokerage
    """
    records = parse_text_block(text, "pa")
    names = {r.name for r in records}
    assert "Page 1 Properties" in names


def test_extract_records_from_json():
    payload = {"results": [{"name": "Jane Doe", "company": "Example Realty", "category": "Residential"}]}
    records = extract_records_from_json(payload, "jane")
    assert records[0].name == "Jane Doe"
    assert records[0].company == "Example Realty"


def test_unique():
    records = parse_text_block("Jane Doe\nExample Realty - Residential\nJane Doe\nExample Realty - Residential", "jane")
    assert len(unique(records)) == 1
