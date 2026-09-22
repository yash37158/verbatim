"""The eval harness, tested. A scoreboard nobody has checked is worse than no scoreboard."""

import json

import pytest

from app import retrieval
from app.db import vec
from app.evals import Case, EvalSet, Outcome, _run_case, score

DIMS = 1536


def outcome(cid, rank=None, dense=None, sparse=None, error=None):
    return Outcome(cid, f"question {cid}", "MSA.pdf", rank, dense, sparse, error)


# ------------------------------------------------------------------ scoring arithmetic


def test_recall_counts_a_hit_anywhere_in_the_top_k():
    m = score([outcome("a", rank=1), outcome("b", rank=8), outcome("c", rank=None)], k=8)
    # score() rounds to 4dp: readable in the saved JSON, far finer than any gate needs.
    assert m["recall_at_k"] == pytest.approx(2 / 3, abs=1e-4)
    assert m["graded"] == 3


def test_mrr_separates_rank_one_from_rank_eight():
    top = score([outcome("a", rank=1), outcome("b", rank=1)], k=8)
    deep = score([outcome("a", rank=8), outcome("b", rank=8)], k=8)
    assert top["recall_at_k"] == deep["recall_at_k"] == 1.0, "recall cannot tell these apart"
    assert top["mrr"] == 1.0
    assert deep["mrr"] == pytest.approx(0.125)


def test_misses_are_listed_so_they_can_be_acted_on():
    m = score([outcome("a", rank=2), outcome("b", rank=None)], k=8)
    assert [x["case_id"] for x in m["misses"]] == ["b"]


def test_an_errored_case_is_excluded_from_the_denominator_not_counted_as_a_miss():
    """An API failure is not evidence that retrieval is bad."""
    m = score([outcome("a", rank=1), outcome("b", error="503 UNAVAILABLE")], k=8)
    assert m["recall_at_k"] == 1.0
    assert m["graded"] == 1 and m["errors"] == 1
    assert m["misses"] == []
    assert m["failed"][0]["case_id"] == "b"


def test_arm_attribution_shows_what_hybrid_retrieval_is_buying():
    m = score(
        [
            outcome("a", rank=1, dense=1, sparse=3),   # both arms found it
            outcome("b", rank=2, dense=None, sparse=1),  # only the keyword arm
            outcome("c", rank=3, dense=2, sparse=None),  # only the dense arm
        ],
        k=8,
    )
    assert m["found_by_both"] == 1
    assert m["rescued_by_keyword"] == 1
    assert m["rescued_by_semantic"] == 1


def test_an_empty_run_scores_zero_rather_than_dividing_by_zero():
    m = score([], k=8)
    assert m["recall_at_k"] == 0.0 and m["mrr"] == 0.0 and m["median_rank"] is None


# ------------------------------------------------------------------ the eval set file


def test_eval_set_round_trips(tmp_path):
    original = EvalSet("space-1", "2026-09-22T00:00:00Z",
                       cases=[Case("c001", "How much notice?", "thirty (30) days", "MSA.pdf")])
    path = tmp_path / "set.json"
    original.save(path)
    assert EvalSet.load(path) == original


def test_an_eval_set_from_a_future_schema_is_refused_not_misread(tmp_path):
    path = tmp_path / "set.json"
    path.write_text(json.dumps({"space_id": "s", "created_at": "t", "schema_version": 99, "cases": []}))
    with pytest.raises(SystemExit, match="schema v99"):
        EvalSet.load(path)


# ------------------------------------------------------------------ against real retrieval


def emb(*weights):
    values = list(weights) + [0.0] * (DIMS - len(weights))
    norm = sum(v * v for v in values) ** 0.5
    return vec([v / norm for v in values])


@pytest.fixture
async def indexed(conn, space, monkeypatch):
    doc = await conn.fetchval(
        """insert into documents (space_id, filename, mime_type, size_bytes, page_count,
                                  sha256, storage_key, status)
           values ($1,'MSA.pdf','application/pdf',1,48,repeat('a',64),'k','ready') returning id""",
        space["mine"],
    )
    for i, text in enumerate([
        "Either party may terminate this Agreement upon thirty (30) days' written notice.",
        "The kitchen is restocked every Friday.",
    ]):
        await conn.execute(
            """insert into chunks (document_id, space_id, ordinal, page_start, page_end,
                                   char_start, char_end, text, embedding)
               values ($1,$2,$3,1,1,0,$4,$5,$6::vector)""",
            doc, space["mine"], i, len(text), text, emb(1, 0) if i == 0 else emb(0, 1),
        )

    async def fake_embed(_text):
        return [float(x) for x in emb(1, 0).strip("[]").split(",")]

    monkeypatch.setattr(retrieval, "embed_query", fake_embed)
    return space["mine"]


async def test_a_gold_quote_is_matched_tolerantly_across_whitespace_and_case(conn, indexed):
    """Gold is text, not a chunk id, so it must survive the same reflow a citation does."""
    case = Case("c001", "how do we exit?", "THIRTY (30)   DAYS'\n WRITTEN notice", "MSA.pdf")
    result = await _run_case(conn, indexed, case, k=8)
    assert result.rank == 1
    assert result.error is None


async def test_a_gold_quote_that_is_not_in_the_corpus_is_a_miss_not_an_error(conn, indexed):
    case = Case("c002", "how do we exit?", "upon sixty (60) days notice", "MSA.pdf")
    result = await _run_case(conn, indexed, case, k=8)
    assert result.rank is None and result.error is None


async def test_a_retrieval_failure_is_recorded_as_an_error_not_a_miss(conn, indexed, monkeypatch):
    async def boom(_text):
        raise RuntimeError("embedding service down")
    monkeypatch.setattr(retrieval, "embed_query", boom)

    result = await _run_case(conn, indexed, Case("c003", "q", "thirty (30) days", "MSA.pdf"), k=8)
    assert result.rank is None
    assert "embedding service down" in result.error
    assert score([result], k=8)["recall_at_k"] == 0.0
    assert score([result], k=8)["graded"] == 0, "an outage must not be scored as bad retrieval"


def test_a_corpus_too_small_to_discriminate_is_flagged():
    """A metric that cannot be wrong is not a useful metric. The tool has to say so."""
    hits = [outcome("a", rank=1)]
    assert score(hits, k=8, corpus=12)["saturated"] is True, "top-8 of 12 discriminates nothing"
    assert score(hits, k=8, corpus=500)["saturated"] is False
    assert score(hits, k=8)["saturated"] is False, "unknown corpus is not a claim of saturation"
    assert score(hits, k=8, corpus=500)["corpus_chunks"] == 500


# ------------------------------------------------------------------ abstention scoring


from app.evals import AbstentionOutcome, score_abstention  # noqa: E402


def abstention(cid, abstained, cited=None, error=None):
    return AbstentionOutcome(cid, f"q {cid}", "MSA-NWS.pdf", abstained, cited or [],
                             "answer text", error)


def test_abstention_accuracy_is_the_share_that_cited_nothing():
    m = score_abstention([
        abstention("a", True),
        abstention("b", True),
        abstention("c", False, cited=["Other.pdf p.3"]),
    ])
    assert m["abstention_accuracy"] == pytest.approx(2 / 3, abs=1e-4)


def test_a_case_that_answered_anyway_records_what_it_leaned_on():
    """Knowing it answered is half the story — you need to see what it cited to tell a
    fabrication from a sibling document that legitimately covered the question."""
    m = score_abstention([abstention("c", False, cited=["Ardent.pdf p.12"])])
    bad = m["answered_anyway"][0]
    assert bad["withheld"] == "MSA-NWS.pdf"
    assert bad["cited"] == ["Ardent.pdf p.12"]
    assert bad["question"] and bad["answer"]


def test_an_errored_case_is_not_counted_as_a_failure_to_abstain():
    m = score_abstention([abstention("a", True), abstention("b", False, error="503")])
    assert m["abstention_accuracy"] == 1.0
    assert m["graded"] == 1 and m["errors"] == 1
    assert m["answered_anyway"] == []


def test_no_cases_scores_zero_rather_than_dividing_by_zero():
    assert score_abstention([])["abstention_accuracy"] == 0.0
