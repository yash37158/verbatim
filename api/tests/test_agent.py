"""The agent loop, marker parsing and citation verification — with the model stubbed out.

The model is the one part we cannot assert on, so it is replaced by a script. Everything
around it (when searches run, what the model is shown, which citations survive) is real.
"""

from types import SimpleNamespace

import pytest

from app import agent, retrieval
from app.agent import MarkerStream
from app.db import vec

DIMS = 1536
PASSAGE = (
    "12.2 Termination for Convenience. Either party may terminate this Agreement for "
    "convenience upon thirty (30) days’ prior written notice to the other party."
)


# ---------------------------------------------------------------- MarkerStream


def drain(stream: MarkerStream, *pieces: str):
    out = []
    for p in pieces:
        out += stream.feed(p)
    return out + stream.flush()


def test_marker_split_across_every_token_boundary():
    whole = "The answer.[[ch_1|a quote]] Next sentence."
    for cut in range(1, len(whole)):
        got = drain(MarkerStream(), whole[:cut], whole[cut:])
        assert [k for k, _ in got].count("cite") == 1, f"broke at cut {cut}"
        assert ("cite", "ch_1|a quote") in got
        assert "".join(v for k, v in got if k == "text") == "The answer. Next sentence."


def test_plain_text_passes_through_untouched():
    assert drain(MarkerStream(), "No markers here at all.") == [("text", "No markers here at all.")]


def test_several_markers_in_one_chunk():
    got = drain(MarkerStream(), "A.[[c1|q1]] B.[[c2|q2]]")
    assert [v for k, v in got if k == "cite"] == ["c1|q1", "c2|q2"]


def test_a_lone_bracket_is_held_then_released_as_text():
    s = MarkerStream()
    # Text is released as soon as it cannot be part of a marker; only the "[" is held.
    assert s.feed("cost [") == [("text", "cost ")]
    assert s.feed("1] per unit") == [("text", "[1] per unit")]


def test_an_unclosed_marker_is_shown_rather_than_swallowed():
    got = drain(MarkerStream(), "Answer.[[ch_1|truncated")
    assert got == [("text", "Answer."), ("text", "[[ch_1|truncated")]


def test_quote_containing_brackets_survives():
    got = drain(MarkerStream(), "X.[[c1|see clause [a] below]]")
    assert ("cite", "c1|see clause [a] below") in got


# ---------------------------------------------------------------- the agent loop


def emb(*weights):
    values = list(weights) + [0.0] * (DIMS - len(weights))
    norm = sum(v * v for v in values) ** 0.5
    return vec([v / norm for v in values])


@pytest.fixture
async def indexed(conn, space, monkeypatch):
    doc = await conn.fetchval(
        """insert into documents (space_id, filename, mime_type, size_bytes, page_count,
                                  sha256, storage_key, status)
           values ($1,'MSA.pdf','application/pdf',1,48,repeat('a', 64),'k','ready') returning id""",
        space["mine"],
    )
    chunk = await conn.fetchval(
        """insert into chunks (document_id, space_id, ordinal, page_start, page_end,
                               char_start, char_end, text, embedding)
           values ($1,$2,0,14,14,0,$3,$4,$5::vector) returning id""",
        doc, space["mine"], len(PASSAGE), PASSAGE, emb(1, 0, 0),
    )

    async def fake_embed(_text):
        return [float(x) for x in emb(1, 0, 0).strip("[]").split(",")]

    monkeypatch.setattr(retrieval, "embed_query", fake_embed)
    return {"space_id": space["mine"], "chunk_id": str(chunk), "document_id": str(doc)}


def script_model(monkeypatch, *, searches: list[str], tokens: list[str]):
    """Stub the model: it issues `searches`, then streams `tokens`."""
    pending = list(searches)
    seen: list[list] = []

    async def fake_generate(contents, *, system_instruction, tools=None, temperature=0.0):
        seen.append(contents)
        if not pending:
            return SimpleNamespace(function_calls=[], candidates=[])
        query = pending.pop(0)
        call = SimpleNamespace(name="search_documents", args={"query": query})
        content = SimpleNamespace(role="model", parts=[])
        return SimpleNamespace(function_calls=[call], candidates=[SimpleNamespace(content=content)])

    async def fake_stream(contents, *, system_instruction, temperature=0.0):
        for t in tokens:  # an async generator, matching gemini.stream
            yield SimpleNamespace(text=t)

    monkeypatch.setattr(agent, "generate", fake_generate)
    monkeypatch.setattr(agent, "stream", fake_stream)
    return seen


async def collect(conn, space_id, question):
    return [e async for e in agent.answer(conn, space_id, question)]


async def test_a_verified_quote_becomes_a_citation_with_the_documents_own_wording(conn, indexed, monkeypatch):
    cid = indexed["chunk_id"]
    script_model(
        monkeypatch,
        searches=["termination for convenience"],
        # the model types a straight apostrophe; the document has a curly one
        tokens=["Either party may terminate on thirty days notice.",
                f"[[{cid}|thirty (30) days' prior written notice]]"],
    )
    events = await collect(conn, indexed["space_id"], "how do we get out of this?")

    search_event = next(e for e in events if e["type"] == "search")
    assert search_event == {"type": "search", "query": "termination for convenience", "hits": 1}

    citation = next(e for e in events if e["type"] == "citation")["citation"]
    assert citation["quote"] == "thirty (30) days’ prior written notice"
    assert citation["document_name"] == "MSA.pdf"
    assert citation["page"] == 14

    done = events[-1]
    assert done["type"] == "done"
    assert done["answer"] == "Either party may terminate on thirty days notice."
    assert done["ungrounded"] is False
    assert done["dropped_citations"] == 0


async def test_a_paraphrased_quote_is_dropped_and_the_answer_is_flagged(conn, indexed, monkeypatch):
    cid = indexed["chunk_id"]
    script_model(
        monkeypatch,
        searches=["termination"],
        tokens=[f"You can cancel any time.[[{cid}|either party may cancel whenever they wish]]"],
    )
    events = await collect(conn, indexed["space_id"], "can we cancel?")

    assert not [e for e in events if e["type"] == "citation"]
    done = events[-1]
    assert done["dropped_citations"] == 1
    assert done["ungrounded"] is True, "it claimed a source and the claim did not hold"
    assert done["answer"] == "You can cancel any time."


async def test_a_chunk_id_the_agent_never_retrieved_is_dropped(conn, indexed, monkeypatch):
    script_model(
        monkeypatch,
        searches=["termination"],
        tokens=["Confident nonsense.[[ch_does_not_exist|thirty (30) days]]"],
    )
    events = await collect(conn, indexed["space_id"], "?")
    assert not [e for e in events if e["type"] == "citation"]
    assert events[-1]["dropped_citations"] == 1


async def test_an_honest_abstention_is_not_flagged_as_ungrounded(conn, indexed, monkeypatch):
    script_model(
        monkeypatch,
        searches=["pricing"],
        tokens=["I couldn't find this in your documents."],
    )
    done = (await collect(conn, indexed["space_id"], "what does it cost?"))[-1]
    assert done["citations"] == []
    assert done["ungrounded"] is False, "no marker was claimed, so nothing failed"


async def test_the_agent_can_search_several_times_before_answering(conn, indexed, monkeypatch):
    script_model(
        monkeypatch,
        searches=["notice period", "termination for convenience", "deletion on termination"],
        tokens=["Three things happen."],
    )
    events = await collect(conn, indexed["space_id"], "walk me through ending the contract")
    assert [e["query"] for e in events if e["type"] == "search"] == [
        "notice period", "termination for convenience", "deletion on termination",
    ]
    assert len(events[-1]["searches"]) == 3


async def test_the_search_loop_is_capped(conn, indexed, monkeypatch):
    script_model(monkeypatch, searches=["q"] * 50, tokens=["Done."])
    events = await collect(conn, indexed["space_id"], "?")
    assert len([e for e in events if e["type"] == "search"]) == agent.settings.max_search_rounds


async def test_citations_are_attributed_to_the_sentence_they_follow(conn, indexed, monkeypatch):
    cid = indexed["chunk_id"]
    script_model(
        monkeypatch,
        searches=["termination"],
        tokens=["First sentence. Second sentence with the fact.",
                f"[[{cid}|Termination for Convenience]]", " Third sentence."],
    )
    events = await collect(conn, indexed["space_id"], "?")
    assert next(e for e in events if e["type"] == "citation")["citation"]["sentence_index"] == 1


async def test_the_answer_prompt_forbids_answering_from_the_wrong_document():
    """Regression, found by the abstention eval: asked about one vendor's warranty with that
    vendor withheld, the agent cited eight other vendors' warranty clauses. Every quote
    verified, so the grounding gate could not catch it — only the prompt can."""
    assert "different party, agreement or document" in agent.ANSWER_SYSTEM
    assert "not an answer" in agent.ANSWER_SYSTEM
