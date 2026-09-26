"""Hybrid retrieval, run against a real Postgres with a real HNSW index."""

import uuid

import pytest

from app import retrieval
from app.db import vec

DIMS = 1536


def emb(*weights: float) -> str:
    """A unit vector pointing along the first few axes — enough to control who wins the
    semantic arm without needing a live embedding model."""
    values = list(weights) + [0.0] * (DIMS - len(weights))
    norm = sum(v * v for v in values) ** 0.5
    return vec([v / norm for v in values])


async def insert_chunk(conn, space_id, document_id, ordinal, text, embedding, page=1):
    return await conn.fetchval(
        """insert into chunks (document_id, space_id, ordinal, page_start, page_end,
                               char_start, char_end, text, embedding)
           values ($1,$2,$3,$4,$4,0,$5,$6,$7::vector) returning id""",
        document_id, space_id, ordinal, page, len(text), text, embedding,
    )


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Every test sets the query vector explicitly; nothing here should call Gemini."""
    async def boom(_):
        raise AssertionError("embed_query should have been stubbed")
    monkeypatch.setattr(retrieval, "embed_query", boom)


def stub_query_vector(monkeypatch, embedding: str):
    async def fake(_text):
        return [float(x) for x in embedding.strip("[]").split(",")]
    monkeypatch.setattr(retrieval, "embed_query", fake)


async def test_semantic_arm_finds_a_paraphrase_with_no_shared_words(conn, space, add_document, monkeypatch):
    doc = await add_document(space["mine"])
    await insert_chunk(conn, space["mine"], doc, 0,
                       "Either party may end this arrangement on thirty days notice.", emb(1, 0, 0))
    await insert_chunk(conn, space["mine"], doc, 1,
                       "The kitchen shall be restocked every Friday afternoon.", emb(0, 1, 0))

    stub_query_vector(monkeypatch, emb(1, 0, 0))
    hits = await retrieval.search(conn, space["mine"], "how do we get out of the contract")

    assert hits[0].text.startswith("Either party")
    assert hits[0].sem_rank == 1
    assert hits[0].kw_rank is None, "no keyword overlap — this is the dense arm's win"


async def test_keyword_arm_rescues_an_exact_identifier_the_vector_misses(conn, space, add_document, monkeypatch):
    doc = await add_document(space["mine"])
    await insert_chunk(conn, space["mine"], doc, 0,
                       "General discussion of billing cadence and net terms.", emb(1, 0, 0))
    await insert_chunk(conn, space["mine"], doc, 1,
                       "Invoice INV-90210 was issued against purchase order PO-4471.", emb(0, 0, 1))

    # The query vector points at the wrong chunk on purpose.
    stub_query_vector(monkeypatch, emb(1, 0, 0))
    hits = await retrieval.search(conn, space["mine"], "INV-90210")

    invoice = next(h for h in hits if "INV-90210" in h.text)
    assert invoice.kw_rank == 1
    assert invoice.sem_rank == max(h.sem_rank for h in hits), "the dense arm ranked it last"
    assert hits[0].chunk_id == invoice.chunk_id, "the keyword arm pulled it to the top anyway"


async def test_a_chunk_matching_both_arms_outranks_one_matching_either(conn, space, add_document, monkeypatch):
    doc = await add_document(space["mine"])
    both = await insert_chunk(conn, space["mine"], doc, 0,
                              "Termination for convenience requires thirty days notice.", emb(1, 0, 0))
    await insert_chunk(conn, space["mine"], doc, 1,
                       "Unrelated prose that happens to sit near the query vector.", emb(0.99, 0.14, 0))
    await insert_chunk(conn, space["mine"], doc, 2,
                       "Termination clauses appear in many agreements.", emb(0, 0, 1))

    stub_query_vector(monkeypatch, emb(1, 0, 0))
    hits = await retrieval.search(conn, space["mine"], "termination notice")

    assert hits[0].chunk_id == both
    assert hits[0].sem_rank is not None and hits[0].kw_rank is not None
    assert hits[0].score > hits[1].score


async def test_another_tenants_perfect_match_is_never_returned(conn, space, add_document, monkeypatch):
    mine_doc = await add_document(space["mine"])
    theirs_doc = await add_document(space["theirs"], filename="Someone-Elses.pdf")
    await insert_chunk(conn, space["mine"], mine_doc, 0, "A merely adequate passage.", emb(0, 1, 0))
    await insert_chunk(conn, space["theirs"], theirs_doc, 0,
                       "Termination for convenience requires thirty days notice.", emb(1, 0, 0))

    stub_query_vector(monkeypatch, emb(1, 0, 0))
    hits = await retrieval.search(conn, space["mine"], "termination notice")

    assert all(h.document_name != "Someone-Elses.pdf" for h in hits)
    assert not any("thirty days notice" in h.text for h in hits)


async def test_document_filter_narrows_the_scope(conn, space, add_document, monkeypatch):
    msa = await add_document(space["mine"], filename="MSA.pdf")
    dpa = await add_document(space["mine"], filename="DPA.pdf")
    await insert_chunk(conn, space["mine"], msa, 0, "Termination requires notice.", emb(1, 0, 0))
    await insert_chunk(conn, space["mine"], dpa, 0, "Termination triggers deletion.", emb(1, 0, 0))

    stub_query_vector(monkeypatch, emb(1, 0, 0))
    hits = await retrieval.search(conn, space["mine"], "termination", document_ids=[dpa])

    assert [h.document_name for h in hits] == ["DPA.pdf"]


async def test_punctuation_heavy_queries_do_not_blow_up_the_tsquery(conn, space, add_document, monkeypatch):
    doc = await add_document(space["mine"])
    await insert_chunk(conn, space["mine"], doc, 0, "Some ordinary text.", emb(1, 0, 0))

    stub_query_vector(monkeypatch, emb(1, 0, 0))
    hits = await retrieval.search(conn, space["mine"], 'what about "clause 12.3" & / OR !!')

    assert len(hits) == 1, "websearch_to_tsquery should absorb this, not raise"


async def test_empty_space_returns_nothing(conn, space, monkeypatch):
    stub_query_vector(monkeypatch, emb(1, 0, 0))
    assert await retrieval.search(conn, space["mine"], "anything") == []


async def test_passage_locator_reads_like_a_citation(conn, space, add_document, monkeypatch):
    doc = await add_document(space["mine"])
    await insert_chunk(conn, space["mine"], doc, 0, "Clause text.", emb(1, 0, 0), page=14)
    stub_query_vector(monkeypatch, emb(1, 0, 0))
    hits = await retrieval.search(conn, space["mine"], "clause")
    assert hits[0].locator == "MSA.pdf, p. 14"


def passage(**kw):
    base = dict(chunk_id=None, document_id=None, document_name="MSA.pdf", page_start=5,
                page_end=6, text="", char_start=10_000, page_offsets=[], score=0.0,
                sem_rank=None, kw_rank=None)
    return retrieval.Passage(**(base | kw))


def test_a_citation_names_the_page_the_quote_is_on_not_the_chunks_first_page():
    """A chunk spanning pages 5-6 must cite p.6 for a quote that sits on page 6."""
    p = passage(page_offsets=[[0, 1], [2000, 2], [4000, 3], [6000, 4], [8000, 5], [10_500, 6]])
    assert p.page_at(0) == 5, "the chunk opens on page 5"
    assert p.page_at(499) == 5
    assert p.page_at(500) == 6, "offset 10500 absolute — the page-6 boundary"
    assert p.page_at(3000) == 6


def test_page_resolution_falls_back_when_a_document_has_no_page_map():
    """Unpaginated formats, and documents ingested before page offsets were stored."""
    assert passage(page_offsets=[]).page_at(0) == 5
    assert passage(page_start=None, page_offsets=[]).page_at(0) is None


async def test_the_keyword_arm_answers_a_full_sentence_question(conn, space, add_document, monkeypatch):
    """Regression: websearch_to_tsquery ANDs every term, so a long natural question matched
    nothing and hybrid search silently collapsed to dense-only. Caught by the retrieval eval."""
    doc = await add_document(space["mine"])
    await insert_chunk(conn, space["mine"], doc, 0,
                       "Processor may engage Sub-processors provided it gives Controller at "
                       "least thirty (30) days notice of any intended addition.", emb(0, 1, 0))
    await insert_chunk(conn, space["mine"], doc, 1,
                       "The kitchen shall be restocked every Friday afternoon.", emb(1, 0, 0))

    stub_query_vector(monkeypatch, emb(1, 0, 0))  # dense arm points at the wrong chunk
    question = "How much time does the Controller have to contest the hiring of a new third party?"
    hits = await retrieval.search(conn, space["mine"], question)

    target = next(h for h in hits if "Sub-processors" in h.text)
    assert target.kw_rank == 1, "no single passage contains every word of the question"


async def test_a_query_with_no_indexable_words_falls_back_to_the_dense_arm(conn, space, add_document, monkeypatch):
    """An all-stopword query renders an empty tsquery; casting '' would raise."""
    doc = await add_document(space["mine"])
    await insert_chunk(conn, space["mine"], doc, 0, "Some ordinary text.", emb(1, 0, 0))

    stub_query_vector(monkeypatch, emb(1, 0, 0))
    hits = await retrieval.search(conn, space["mine"], "the and of a")
    assert len(hits) == 1 and hits[0].kw_rank is None


async def test_a_throttled_embedding_api_degrades_to_keyword_search_not_to_no_answer(
    conn, space, add_document, monkeypatch
):
    """A rate limit on the embedding provider must not turn into 'no answer'. The keyword
    arm needs no vectors, so search carries on with that alone."""
    from google.genai.errors import ClientError

    doc = await add_document(space["mine"])
    await insert_chunk(conn, space["mine"], doc, 0,
                       "Either party may terminate this Agreement upon thirty (30) days notice.",
                       emb(1, 0, 0))

    async def throttled(_text):
        raise ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                          "message": "quota"}})
    monkeypatch.setattr(retrieval, "embed_query", throttled)

    hits = await retrieval.search(conn, space["mine"], "terminate thirty days notice")
    assert hits, "keyword search should still find it"
    assert hits[0].sem_rank is None, "the dense arm had no query vector to work with"
    assert hits[0].kw_rank == 1


async def test_a_non_transient_embedding_error_still_raises(conn, space, monkeypatch):
    """A 400 is a bug in our request, not a provider hiccup. Hiding it behind keyword search
    would make it invisible."""
    from google.genai.errors import ClientError

    async def broken(_text):
        raise ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                          "message": "bad request"}})
    monkeypatch.setattr(retrieval, "embed_query", broken)

    with pytest.raises(ClientError):
        await retrieval.search(conn, space["mine"], "anything")


async def test_unembedded_chunks_are_found_by_keyword_and_skipped_by_the_dense_arm(
    conn, space, add_document, monkeypatch
):
    """Chunks land before their vectors do. Until the vector arrives, wording finds them and
    meaning does not — and neither arm errors on the NULL."""
    doc = await add_document(space["mine"])
    await conn.execute(
        """insert into chunks (document_id, space_id, ordinal, page_start, page_end,
                               char_start, char_end, text)
           values ($1,$2,0,1,1,0,40,'Sub-processors require thirty days notice.')""",
        doc, space["mine"],
    )
    stub_query_vector(monkeypatch, emb(1, 0, 0))
    hits = await retrieval.search(conn, space["mine"], "sub-processors notice")
    assert len(hits) == 1
    assert hits[0].kw_rank == 1 and hits[0].sem_rank is None
