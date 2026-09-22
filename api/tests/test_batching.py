"""Embedding requests are bounded by tokens, not item count."""

from app.gemini import batches


def test_a_batch_is_closed_before_it_exceeds_the_token_ceiling():
    text = "x" * 4000  # 4000 // 4 + 1 = 1001 estimated tokens
    assert [len(g) for g in batches([text] * 10, max_tokens=3100, max_items=100)] == [3, 3, 3, 1]
    # 3003 > 3000, so the third does not fit — the ceiling is never exceeded, only met
    assert [len(g) for g in batches([text] * 10, max_tokens=3000, max_items=100)] == [2] * 5


def test_the_item_cap_still_applies_to_small_texts():
    groups = batches(["x"] * 250, max_tokens=1_000_000, max_items=100)
    assert [len(g) for g in groups] == [100, 100, 50]


def test_nothing_is_dropped_or_duplicated():
    texts = [f"text-{i}" * 200 for i in range(37)]
    flat = [t for g in batches(texts, max_tokens=2000, max_items=8) for t in g]
    assert flat == texts, "order and contents must survive batching — vectors zip back by index"


def test_a_single_oversized_text_goes_on_its_own_rather_than_being_dropped():
    groups = batches(["y" * 100_000, "small"], max_tokens=1000, max_items=100)
    assert len(groups) == 2 and groups[0] == ["y" * 100_000]


def test_no_input_means_no_requests():
    assert batches([], max_tokens=1000, max_items=10) == []
