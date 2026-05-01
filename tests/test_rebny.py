from donor_vetting.models import RebnyCandidate
from donor_vetting.rebny import extract_member_candidates, classify_rebny_match, score_rebny_candidate


def test_extract_member_candidates_from_card_html():
    html = """
    <html><body>
      <article class='member-card'>
        <h3>Jane Q. Doe</h3>
        <p>Example Realty - Residential - Broker</p>
        <p>Principal Broker</p>
      </article>
    </body></html>
    """
    candidates = extract_member_candidates(html, "https://www.rebny.com/members/?search=Jane+Doe")
    assert len(candidates) == 1
    assert candidates[0].name == "Jane Q. Doe"
    assert "Example Realty" in candidates[0].company


def test_count_string_alone_is_not_a_match():
    html = """
    <html><body>
      <h1>Member Directory</h1>
      <p>Search By Name</p>
      <p>1 Members</p>
      <h1>No Search Results Found</h1>
    </body></html>
    """
    candidates = extract_member_candidates(html)
    result, _ = classify_rebny_match("John", "Smith", candidates)
    assert candidates == []
    assert result.match is False
    assert result.status == "not found"


def test_exact_first_last_is_found_even_with_middle_initial():
    candidates = [RebnyCandidate(name="Jane Q. Doe", company="Example Realty")]
    result, _ = classify_rebny_match("Jane", "Doe", candidates)
    assert result.status == "FOUND"
    assert result.match is True
    assert result.confidence >= 96


def test_initial_only_is_review_not_found():
    candidate = RebnyCandidate(name="J. Doe", company="Example Realty")
    score, reason = score_rebny_candidate("Jane", "Doe", candidate)
    result, _ = classify_rebny_match("Jane", "Doe", [candidate])
    assert score >= 86
    assert "initial" in reason
    assert result.status == "review"
    assert result.match is False
    assert result.needs_review is True


def test_wrong_person_is_not_found():
    candidates = [RebnyCandidate(name="Janet Roe", company="Example Realty")]
    result, _ = classify_rebny_match("Jane", "Doe", candidates)
    assert result.match is False
    assert result.status == "not found"
