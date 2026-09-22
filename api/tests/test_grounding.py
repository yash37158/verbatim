from app.grounding import locate, normalize, verify

SOURCE = (
    "12.2 Termination for Convenience.\n Either party may terminate this Agreement for "
    "convenience upon thirty (30) days’ prior written notice to the other party.\n"
    "No termination fee shall be payable."
)


def test_exact_quote_is_returned_from_the_source():
    q = "Either party may terminate this Agreement for convenience"
    assert verify(q, SOURCE) == q


def test_reflowed_whitespace_still_matches():
    q = "Termination   for\n\n  Convenience."
    assert verify(q, SOURCE) == "Termination for Convenience."


def test_straight_apostrophe_matches_a_curly_one_and_the_source_wins():
    q = "thirty (30) days' prior written notice"
    got = verify(q, SOURCE)
    assert got == "thirty (30) days’ prior written notice"
    assert "’" in got, "the user must see the document's characters, not the model's"


def test_case_difference_matches_but_the_source_casing_is_shown():
    assert verify("EITHER PARTY MAY TERMINATE", SOURCE) == "Either party may terminate"


def test_a_paraphrase_is_rejected():
    assert verify("Either party can cancel the contract whenever they like", SOURCE) is None


def test_a_plausible_but_absent_clause_is_rejected():
    assert verify("upon sixty (60) days prior written notice", SOURCE) is None


def test_an_empty_or_whitespace_quote_is_rejected():
    assert verify("", SOURCE) is None
    assert verify("   \n ", SOURCE) is None


def test_span_maps_back_to_the_original_offsets():
    span = locate("No termination fee", SOURCE)
    assert span is not None
    assert SOURCE[span[0] : span[1]] == "No termination fee"


def test_normalize_collapses_runs_and_trims():
    assert normalize("  A\t\tB \n C  ") == "a b c"
