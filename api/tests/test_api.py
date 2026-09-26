"""End-to-end over HTTP against the real database and the real ingestion pipeline.

Only the two calls to Google are stubbed: embeddings and generation. Parsing, chunking,
the job queue, pgvector search, quote verification, auth and tenancy all run for real.
"""

import json
import secrets
from types import SimpleNamespace

import httpx
import pymupdf
import pytest
import pytest_asyncio

from app import agent, gemini, ingest, retrieval
from app.config import settings
from app.db import acquire, disconnect
from app.main import app

CLAUSE = (
    "12.2 Termination for Convenience. Either party may terminate this Agreement for "
    "convenience upon thirty (30) days prior written notice to the other party. "
    "No termination fee shall be payable under this Section."
)
DIMS = 1536


def make_pdf(body: str, pages: int = 1) -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(40, 40, 550, 780), f"Page {i + 1}. {body}", fontsize=11)
    return doc.tobytes()


def blank_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page()
    return doc.tobytes()


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """No network, and no writes outside the test's own directory."""
    monkeypatch.setattr(settings, "storage_dir", tmp_path / "storage")
    monkeypatch.setattr(settings, "run_worker", False)

    async def fake_embed_batch(texts, task_type="RETRIEVAL_DOCUMENT"):
        return [[1.0] + [0.0] * (DIMS - 1) for _ in texts]

    async def fake_embed_query(_text):
        return [1.0] + [0.0] * (DIMS - 1)

    monkeypatch.setattr(ingest, "embed_batch", fake_embed_batch)
    monkeypatch.setattr(retrieval, "embed_query", fake_embed_query)
    monkeypatch.setattr(gemini, "_client", object())  # nothing should construct a real client


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await disconnect()


@pytest_asyncio.fixture
async def account():
    """A real user row; torn down by cascade so the shared database stays clean."""
    made = []

    async def make():
        async with acquire() as conn:
            key = "vb_test_" + secrets.token_urlsafe(16)
            uid = await conn.fetchval(
                "insert into users (email, api_key) values ($1, $2) returning id",
                f"{key}@example.com", key,
            )
            made.append(uid)
            return {"Authorization": f"Bearer {key}"}

    yield make
    async with acquire() as conn:
        for uid in made:
            await conn.execute("delete from users where id = $1", uid)


def sse(text: str) -> list[dict]:
    return [
        json.loads(frame[len("data:") :].strip())
        for frame in text.split("\n\n")
        if frame.startswith("data:")
    ]


def script_model(monkeypatch, *, searches, tokens):
    """One tool-calling streamed turn per search, then the answer streamed as tokens.
    Same shape as the real Gemini stream: chunks with parts carrying function_call or text."""
    pending = list(searches)

    def chunk(*parts):
        return SimpleNamespace(candidates=[SimpleNamespace(
            content=SimpleNamespace(role="model", parts=list(parts)))])

    async def fake_stream(contents, *, system_instruction, tools=None, temperature=0.0):
        if pending and tools is not None:
            query = pending.pop(0)
            yield chunk(SimpleNamespace(
                function_call=SimpleNamespace(name="search_documents", args={"query": query}),
                text=None))
            return
        for t in tokens:
            yield chunk(SimpleNamespace(function_call=None, text=t))

    monkeypatch.setattr(agent, "stream", fake_stream)


# ---------------------------------------------------------------- auth


async def test_health_needs_no_token(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


async def test_every_data_route_rejects_a_missing_or_bogus_token(client):
    assert (await client.get("/api/spaces")).status_code == 401
    r = await client.get("/api/spaces", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


# ---------------------------------------------------------------- the whole flow


async def test_upload_ingest_search_and_answer(client, account, monkeypatch):
    auth = await account()

    space = (await client.post("/api/spaces", json={"name": "Contracts"}, headers=auth)).json()
    r = await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("MSA.pdf", make_pdf(CLAUSE, pages=3), "application/pdf")},
        headers=auth,
    )
    assert r.status_code == 202
    doc = r.json()
    assert doc["status"] == "queued"

    assert await ingest.process_one() is True          # the worker claims and ingests it
    assert await ingest.process_one() is False         # queue drained

    docs = (await client.get(f"/api/spaces/{space['id']}/documents", headers=auth)).json()
    assert docs[0]["status"] == "ready"
    assert docs[0]["page_count"] == 3

    async with acquire() as conn:
        chunks = await conn.fetch(
            "select id, text, page_start from chunks where document_id = $1 order by ordinal",
            doc["id"],
        )
    assert chunks, "ingestion produced no chunks"
    quotable = next(c for c in chunks if "thirty (30) days prior written notice" in c["text"])

    convo = (
        await client.post(f"/api/spaces/{space['id']}/conversations", headers=auth)
    ).json()
    script_model(
        monkeypatch,
        searches=["termination for convenience"],
        tokens=[
            "Either party may terminate on thirty days notice.",
            f"[[{quotable['id']}|thirty (30) days prior written notice]]",
        ],
    )
    r = await client.post(
        f"/api/conversations/{convo['id']}/messages",
        json={"content": "how do we get out of this contract?"},
        headers=auth,
    )
    assert r.status_code == 200
    events = sse(r.text)

    assert [e["type"] for e in events][0] == "search"
    citation = next(e for e in events if e["type"] == "citation")["citation"]
    assert citation["quote"] == "thirty (30) days prior written notice"
    assert citation["document_name"] == "MSA.pdf"
    assert citation["page"] is not None

    done = events[-1]
    assert done["type"] == "done" and done["ungrounded"] is False and done["message_id"]

    stored = (await client.get(f"/api/conversations/{convo['id']}/messages", headers=auth)).json()
    assert [m["role"] for m in stored] == ["user", "assistant"]
    assert stored[1]["citations"][0]["quote"] == "thirty (30) days prior written notice"

    # PRD §9 tracks grounding health. It is computed per answer and has to be kept.
    async with acquire() as conn:
        row = await conn.fetchrow(
            "select ungrounded, dropped_citations, model, latency_ms from messages "
            "where conversation_id = $1 and role = 'assistant'", convo["id"])
    assert row["ungrounded"] is False
    assert row["dropped_citations"] == 0
    assert row["model"] == settings.gemini_model, "which model produced this is part of the metric"
    assert row["latency_ms"] >= 0


async def test_a_scanned_pdf_fails_with_an_explanation_and_is_not_retried(client, account):
    auth = await account()
    space = (await client.post("/api/spaces", json={"name": "Scans"}, headers=auth)).json()
    await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("scan.pdf", blank_pdf(), "application/pdf")},
        headers=auth,
    )
    await ingest.process_one()

    doc = (await client.get(f"/api/spaces/{space['id']}/documents", headers=auth)).json()[0]
    assert doc["status"] == "failed"
    assert "scan" in doc["error"].lower()
    assert await ingest.process_one() is False, "a bad document must not be retried forever"


async def test_reuploading_the_same_bytes_is_a_no_op(client, account):
    auth = await account()
    space = (await client.post("/api/spaces", json={"name": "Dupes"}, headers=auth)).json()
    payload = make_pdf(CLAUSE)
    first = await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("a.pdf", payload, "application/pdf")}, headers=auth,
    )
    second = await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("a-copy.pdf", payload, "application/pdf")}, headers=auth,
    )
    assert second.json()["duplicate"] is True
    assert second.json()["id"] == first.json()["id"]


async def test_one_tenant_cannot_reach_anothers_space_document_or_chunk(client, account, monkeypatch):
    mine, theirs = await account(), await account()
    space = (await client.post("/api/spaces", json={"name": "Private"}, headers=mine)).json()
    doc = (
        await client.post(
            f"/api/spaces/{space['id']}/documents",
            files={"file": ("MSA.pdf", make_pdf(CLAUSE), "application/pdf")}, headers=mine,
        )
    ).json()
    await ingest.process_one()
    async with acquire() as conn:
        chunk_id = await conn.fetchval(
            "select id from chunks where document_id = $1 limit 1", doc["id"]
        )

    assert (await client.get(f"/api/spaces/{space['id']}", headers=theirs)).status_code == 404
    assert (await client.get(f"/api/spaces/{space['id']}/documents", headers=theirs)).status_code == 404
    assert (await client.delete(f"/api/documents/{doc['id']}", headers=theirs)).status_code == 404
    assert (await client.get(f"/api/chunks/{chunk_id}", headers=theirs)).status_code == 404
    assert (await client.get(f"/api/chunks/{chunk_id}", headers=mine)).status_code == 200
    assert (await client.get(f"/api/spaces", headers=theirs)).json() == []


async def test_an_empty_upload_is_rejected(client, account):
    auth = await account()
    space = (await client.post("/api/spaces", json={"name": "S"}, headers=auth)).json()
    r = await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("empty.pdf", b"", "application/pdf")}, headers=auth,
    )
    assert r.status_code == 400


async def test_deleting_a_document_removes_its_chunks(client, account):
    auth = await account()
    space = (await client.post("/api/spaces", json={"name": "S"}, headers=auth)).json()
    doc = (
        await client.post(
            f"/api/spaces/{space['id']}/documents",
            files={"file": ("MSA.pdf", make_pdf(CLAUSE), "application/pdf")}, headers=auth,
        )
    ).json()
    await ingest.process_one()

    async with acquire() as conn:
        assert await conn.fetchval("select count(*) from chunks where document_id=$1", doc["id"]) > 0
    assert (await client.delete(f"/api/documents/{doc['id']}", headers=auth)).status_code == 204
    async with acquire() as conn:
        assert await conn.fetchval("select count(*) from chunks where document_id=$1", doc["id"]) == 0


async def test_a_dropped_citation_is_recorded_not_just_hidden(client, account, monkeypatch):
    """A rising drop rate is the early warning that the model has drifted from the source.
    It is only a warning if it is written down."""
    auth = await account()
    space = (await client.post("/api/spaces", json={"name": "Drift"}, headers=auth)).json()
    await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("MSA.pdf", make_pdf(CLAUSE), "application/pdf")}, headers=auth,
    )
    await ingest.process_one()
    async with acquire() as conn:
        chunk_id = await conn.fetchval("select id from chunks limit 1")

    convo = (await client.post(f"/api/spaces/{space['id']}/conversations", headers=auth)).json()
    script_model(
        monkeypatch,
        searches=["termination"],
        tokens=[f"You may cancel at will.[[{chunk_id}|either party may cancel at will]]"],
    )
    r = await client.post(
        f"/api/conversations/{convo['id']}/messages",
        json={"content": "can we cancel?"}, headers=auth,
    )
    assert next(e for e in sse(r.text) if e["type"] == "done")["dropped_citations"] == 1

    async with acquire() as conn:
        row = await conn.fetchrow(
            "select ungrounded, dropped_citations from messages "
            "where conversation_id = $1 and role = 'assistant'", convo["id"])
    assert row["dropped_citations"] == 1
    assert row["ungrounded"] is True


async def test_a_document_over_the_page_limit_is_refused_before_it_costs_anything(
    client, account, monkeypatch
):
    """Rejecting after parse but before embedding: a second of CPU instead of ten minutes
    of API spend discovering the file was never reasonable."""
    monkeypatch.setattr(settings, "max_pages", 5)
    embedded: list[int] = []

    async def counting_embed(texts, task_type="RETRIEVAL_DOCUMENT"):
        embedded.append(len(texts))
        return [[1.0] + [0.0] * (DIMS - 1) for _ in texts]

    monkeypatch.setattr(ingest, "embed_batch", counting_embed)

    auth = await account()
    space = (await client.post("/api/spaces", json={"name": "Huge"}, headers=auth)).json()
    await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("tome.pdf", make_pdf(CLAUSE, pages=9), "application/pdf")}, headers=auth,
    )
    await ingest.process_one()

    doc = (await client.get(f"/api/spaces/{space['id']}/documents", headers=auth)).json()[0]
    assert doc["status"] == "failed"
    assert "9 pages exceeds the 5-page limit" in doc["error"]
    assert embedded == [], "nothing should have been sent for embedding"
    assert await ingest.process_one() is False, "an oversized file must not be retried"


async def test_document_count_is_not_inflated_by_conversation_history(client, account, monkeypatch):
    """Regression: the spaces list joined documents and messages in one query, so
    count(documents) returned documents x messages. Two files and nineteen questions
    rendered as '38 documents' in the UI."""
    auth = await account()
    space = (await client.post("/api/spaces", json={"name": "Busy"}, headers=auth)).json()
    for name in ("a.pdf", "b.pdf"):
        await client.post(
            f"/api/spaces/{space['id']}/documents",
            files={"file": (name, make_pdf(CLAUSE + name), "application/pdf")}, headers=auth,
        )
        await ingest.process_one()

    convo = (await client.post(f"/api/spaces/{space['id']}/conversations", headers=auth)).json()
    script_model(monkeypatch, searches=[], tokens=["An answer."])
    for i in range(3):
        await client.post(f"/api/conversations/{convo['id']}/messages",
                          json={"content": f"question {i}"}, headers=auth)

    listed = next(s for s in (await client.get("/api/spaces", headers=auth)).json()
                  if s["id"] == space["id"])
    single = (await client.get(f"/api/spaces/{space['id']}", headers=auth)).json()
    assert listed["document_count"] == 2, "six messages must not multiply the document count"
    assert single["document_count"] == 2
    assert listed["last_activity_at"] is not None


async def test_deleting_a_document_removes_the_stored_file_too(client, account):
    """PRD §8 promises deletion is real. Dropping the row while the bytes sit on disk is
    not deletion, and would not satisfy an erasure request."""
    auth = await account()
    space = (await client.post("/api/spaces", json={"name": "Erasure"}, headers=auth)).json()
    doc = (await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("MSA.pdf", make_pdf(CLAUSE), "application/pdf")}, headers=auth,
    )).json()
    await ingest.process_one()

    async with acquire() as conn:
        key = await conn.fetchval("select storage_key from documents where id = $1", doc["id"])
    stored = ingest.storage_path(key)
    assert stored.exists()

    assert (await client.delete(f"/api/documents/{doc['id']}", headers=auth)).status_code == 204
    assert not stored.exists(), "the bytes outlived the record"


async def test_shared_bytes_survive_until_the_last_reference_goes(client, account):
    """Content-addressed storage means the same file in two Spaces is one file on disk.
    Deleting one copy must not pull the rug from under the other."""
    auth = await account()
    payload = make_pdf(CLAUSE)
    docs = []
    for name in ("One", "Two"):
        space = (await client.post("/api/spaces", json={"name": name}, headers=auth)).json()
        docs.append((await client.post(
            f"/api/spaces/{space['id']}/documents",
            files={"file": ("MSA.pdf", payload, "application/pdf")}, headers=auth,
        )).json())
    assert docs[0]["id"] != docs[1]["id"]

    async with acquire() as conn:
        key = await conn.fetchval("select storage_key from documents where id = $1", docs[0]["id"])
    stored = ingest.storage_path(key)
    assert stored.exists()

    await client.delete(f"/api/documents/{docs[0]['id']}", headers=auth)
    assert stored.exists(), "the second Space still points at these bytes"

    await client.delete(f"/api/documents/{docs[1]['id']}", headers=auth)
    assert not stored.exists(), "last reference gone, so the bytes should be too"


async def test_a_rate_limited_document_waits_instead_of_being_written_off(
    client, account, monkeypatch
):
    """A daily quota needs hours; the in-process backoff covers seconds. And crucially the
    document stays *ready* while it waits — keyword search works the whole time."""
    from google.genai.errors import ClientError

    async def rate_limited(texts, task_type="RETRIEVAL_DOCUMENT"):
        raise ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                          "message": "You exceeded your current quota"}})

    monkeypatch.setattr(ingest, "embed_batch", rate_limited)

    auth = await account()
    space = (await client.post("/api/spaces", json={"name": "Throttled"}, headers=auth)).json()
    doc = (await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("MSA.pdf", make_pdf(CLAUSE), "application/pdf")}, headers=auth,
    )).json()

    assert await ingest.process_one() is True      # parses, never touches the API
    assert await ingest.embed_pending() is True    # tries to embed, gets throttled

    async with acquire() as conn:
        row = await conn.fetchrow(
            "select status, attempts, error, retry_after from documents where id = $1", doc["id"])
    assert row["status"] == "ready", "throttled embedding must not take the document offline"
    assert row["attempts"] == 1, "one parse attempt; throttling does not count"
    assert row["retry_after"] is not None
    assert "Rate limited" in row["error"] and "RESOURCE_EXHAUSTED" not in row["error"]
    assert await ingest.embed_pending() is False, "not due yet, so the worker leaves it alone"


async def test_a_failed_document_can_be_retried(client, account, monkeypatch):
    auth = await account()
    space = (await client.post("/api/spaces", json={"name": "Retry"}, headers=auth)).json()
    await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("scan.pdf", blank_pdf(), "application/pdf")}, headers=auth,
    )
    await ingest.process_one()
    doc = (await client.get(f"/api/spaces/{space['id']}/documents", headers=auth)).json()[0]
    assert doc["status"] == "failed"

    r = await client.post(f"/api/documents/{doc['id']}/retry", headers=auth)
    assert r.status_code == 202 and r.json()["status"] == "queued"
    assert r.json()["error"] is None
    assert await ingest.process_one() is True, "the retry put it back on the queue"


async def test_a_document_waiting_on_quota_can_be_tried_immediately(client, account, monkeypatch):
    """Once the user knows quota is back, "next attempt in 15 minutes" should not be binding."""
    from google.genai.errors import ClientError

    async def throttled(texts, task_type="RETRIEVAL_DOCUMENT"):
        raise ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                          "message": "quota. 'retryDelay': '600s'"}})

    monkeypatch.setattr(ingest, "embed_batch", throttled)
    auth = await account()
    space = (await client.post("/api/spaces", json={"name": "Waiting"}, headers=auth)).json()
    doc = (await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("MSA.pdf", make_pdf(CLAUSE), "application/pdf")}, headers=auth,
    )).json()
    await ingest.process_one()
    await ingest.embed_pending()
    assert await ingest.embed_pending() is False, "it is waiting, so the worker leaves it alone"

    r = await client.post(f"/api/documents/{doc['id']}/retry", headers=auth)
    assert r.status_code == 202, "a ready document waiting on quota is exactly what retry is for"
    assert r.json()["retry_after"] is None, "the wait is cleared"
    assert r.json()["status"] == "ready", "and it was never taken offline to do it"
    assert await ingest.embed_pending() is True, "the worker picks it straight up"


async def test_retrying_a_healthy_document_is_refused(client, account):
    auth = await account()
    space = (await client.post("/api/spaces", json={"name": "Healthy"}, headers=auth)).json()
    doc = (await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("MSA.pdf", make_pdf(CLAUSE), "application/pdf")}, headers=auth,
    )).json()
    await ingest.process_one()
    r = await client.post(f"/api/documents/{doc['id']}/retry", headers=auth)
    assert r.status_code == 409


async def test_the_retry_time_reaches_the_client(client, account, monkeypatch):
    """'It will retry' without a time reads as 'it is stuck'. The UI needs the timestamp."""
    from google.genai.errors import ClientError

    async def throttled(texts, task_type="RETRIEVAL_DOCUMENT"):
        raise ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
            "message": "quota. 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier',"
                       " 'quotaValue': '20'. 'retryDelay': '38s'"}})

    monkeypatch.setattr(ingest, "embed_batch", throttled)
    auth = await account()
    space = (await client.post("/api/spaces", json={"name": "Timed"}, headers=auth)).json()
    await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("MSA.pdf", make_pdf(CLAUSE), "application/pdf")}, headers=auth,
    )
    await ingest.process_one()
    await ingest.embed_pending()

    doc = (await client.get(f"/api/spaces/{space['id']}/documents", headers=auth)).json()[0]
    assert doc["status"] == "ready"
    assert doc["retry_after"] is not None, "the client cannot show a time it was never sent"
    assert doc["error"] == "Daily free-tier limit reached (20 requests)."
    assert doc["indexed_chunks"] == 0 and doc["total_chunks"] > 0

    async with acquire() as conn:
        minutes = await conn.fetchval(
            "select extract(epoch from (retry_after - now())) / 60 from documents where id = $1",
            doc["id"])
    # a daily cap will not clear in the 38 seconds Google suggests, so we wait longer
    assert 10 < minutes <= 15


async def test_embedding_progress_survives_a_rate_limit_mid_document(client, account, monkeypatch):
    """A long document on a throttled key must make forward progress. Re-embedding from
    zero on every retry means each attempt gets exactly as far as the last, forever."""
    from google.genai.errors import ClientError

    calls = {"n": 0}

    async def fails_after_two_batches(texts, task_type="RETRIEVAL_DOCUMENT"):
        calls["n"] += 1
        if calls["n"] > 2:
            raise ClientError(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                              "message": "quota. 'retryDelay': '30s'"}})
        return [[1.0] + [0.0] * (DIMS - 1) for _ in texts]

    monkeypatch.setattr(ingest, "embed_batch", fails_after_two_batches)
    monkeypatch.setattr(settings, "embed_batch", 3)  # small batches, so there are several

    auth = await account()
    space = (await client.post("/api/spaces", json={"name": "Long"}, headers=auth)).json()
    doc = (await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("long.pdf", make_pdf(CLAUSE, pages=12), "application/pdf")}, headers=auth,
    )).json()

    await ingest.process_one()
    async with acquire() as conn:
        after_first = await conn.fetchval(
            "select count(*) from chunks where document_id = $1", doc["id"])
    assert after_first > 0, "the batches that succeeded should be on disk"

    # quota returns; the next run must pick up where it stopped, not start over
    embedded: list[int] = []

    async def succeeds(texts, task_type="RETRIEVAL_DOCUMENT"):
        embedded.append(len(texts))
        return [[1.0] + [0.0] * (DIMS - 1) for _ in texts]

    monkeypatch.setattr(ingest, "embed_batch", succeeds)
    await client.post(f"/api/documents/{doc['id']}/retry", headers=auth)
    await ingest.process_one()

    async with acquire() as conn:
        row = await conn.fetchrow(
            "select status, (select count(*) from chunks where document_id = $1) as n "
            "from documents where id = $1", doc["id"])
    assert row["status"] == "ready"
    assert sum(embedded) == row["n"] - after_first, "already-embedded chunks were not redone"


async def test_a_document_is_searchable_by_keyword_before_any_embedding_happens(
    client, account, monkeypatch
):
    """The point of splitting parsing from embedding. A user should be able to ask a
    question seconds after upload, not after every embedding request clears a rate limit."""
    calls = {"n": 0}

    async def never_reached(texts, task_type="RETRIEVAL_DOCUMENT"):
        calls["n"] += 1
        return [[1.0] + [0.0] * (DIMS - 1) for _ in texts]

    monkeypatch.setattr(ingest, "embed_batch", never_reached)

    auth = await account()
    space = (await client.post("/api/spaces", json={"name": "Instant"}, headers=auth)).json()
    doc = (await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("MSA.pdf", make_pdf(CLAUSE), "application/pdf")}, headers=auth,
    )).json()

    assert await ingest.process_one() is True
    assert calls["n"] == 0, "parsing must not touch the embedding API"

    listed = (await client.get(f"/api/spaces/{space['id']}/documents", headers=auth)).json()[0]
    assert listed["status"] == "ready", "searchable the moment chunks land"
    assert listed["total_chunks"] > 0
    assert listed["indexed_chunks"] == 0, "no vectors yet — and that is fine"

    # The keyword arm needs no vectors. The query stub points the dense arm nowhere.
    async with acquire() as conn:
        hits = await retrieval.search(conn, space["id"], "thirty (30) days")
    assert hits, "keyword search found it with zero embeddings in the table"
    assert hits[0].sem_rank is None and hits[0].kw_rank == 1

    # Now the backfill runs and the semantic arm comes online.
    assert await ingest.embed_pending() is True
    listed = (await client.get(f"/api/spaces/{space['id']}/documents", headers=auth)).json()[0]
    assert listed["indexed_chunks"] == listed["total_chunks"]
    assert await ingest.embed_pending() is False, "nothing left to embed"


# ------------------------------------------------------------------ F5: chat history


async def _conversation_with_one_exchange(client, auth, monkeypatch, space_name="History"):
    space = (await client.post("/api/spaces", json={"name": space_name}, headers=auth)).json()
    await client.post(
        f"/api/spaces/{space['id']}/documents",
        files={"file": ("MSA.pdf", make_pdf(CLAUSE), "application/pdf")}, headers=auth,
    )
    await ingest.process_one()
    await ingest.embed_pending()
    convo = (await client.post(f"/api/spaces/{space['id']}/conversations", headers=auth)).json()
    async with acquire() as conn:
        chunk_id = await conn.fetchval("select id from chunks limit 1")
    script_model(monkeypatch, searches=["termination"],
                 tokens=[f"Thirty days.[[{chunk_id}|thirty (30) days prior written notice]]"])
    await client.post(f"/api/conversations/{convo['id']}/messages",
                      json={"content": "how much notice?"}, headers=auth)
    return space, convo


async def test_stored_messages_carry_everything_needed_to_restore_a_conversation(
    client, account, monkeypatch
):
    """Reload must bring back the citations *and* what the agent searched for — the UI shows
    both, and a history that loses either is not the conversation the user had."""
    auth = await account()
    _, convo = await _conversation_with_one_exchange(client, auth, monkeypatch)

    msgs = (await client.get(f"/api/conversations/{convo['id']}/messages", headers=auth)).json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    reply = msgs[1]
    assert reply["citations"][0]["quote"] == "thirty (30) days prior written notice"
    assert reply["searches"] == [{"query": "termination", "hits": 1}]
    assert reply["created_at"]


async def test_conversations_list_orders_by_last_activity_with_counts(client, account, monkeypatch):
    auth = await account()
    space, first = await _conversation_with_one_exchange(client, auth, monkeypatch)
    second = (await client.post(f"/api/spaces/{space['id']}/conversations", headers=auth)).json()

    listed = (await client.get(f"/api/spaces/{space['id']}/conversations", headers=auth)).json()
    assert [c["id"] for c in listed][:2] == [second["id"], first["id"]], "newest activity first"
    by_id = {c["id"]: c for c in listed}
    assert by_id[first["id"]]["message_count"] == 2
    assert by_id[first["id"]]["title"] == "how much notice?", "auto-titled from the question"
    assert by_id[second["id"]]["message_count"] == 0


async def test_a_conversation_can_be_renamed_and_deleted(client, account, monkeypatch):
    auth = await account()
    space, convo = await _conversation_with_one_exchange(client, auth, monkeypatch)

    r = await client.patch(f"/api/conversations/{convo['id']}", json={"title": "  Notice period  "},
                           headers=auth)
    assert r.status_code == 200 and r.json()["title"] == "Notice period"

    r = await client.patch(f"/api/conversations/{convo['id']}", json={"title": ""}, headers=auth)
    assert r.status_code == 422, "an empty title is not a rename"

    assert (await client.delete(f"/api/conversations/{convo['id']}", headers=auth)).status_code == 204
    assert (await client.get(f"/api/conversations/{convo['id']}/messages", headers=auth)).status_code == 404
    async with acquire() as conn:
        assert await conn.fetchval("select count(*) from messages where conversation_id = $1",
                                   convo["id"]) == 0, "messages cascade"


async def test_another_tenant_cannot_rename_or_delete_my_conversation(client, account, monkeypatch):
    mine, theirs = await account(), await account()
    _, convo = await _conversation_with_one_exchange(client, mine, monkeypatch)

    assert (await client.patch(f"/api/conversations/{convo['id']}", json={"title": "x"},
                               headers=theirs)).status_code == 404
    assert (await client.delete(f"/api/conversations/{convo['id']}", headers=theirs)).status_code == 404
    assert (await client.get(f"/api/conversations/{convo['id']}/messages",
                             headers=mine)).status_code == 200, "and mine is untouched"
