"""Agentic RAG.

The difference from single-shot RAG: nothing here decides in advance what to retrieve.
The model is given a search tool and calls it as many times as the question needs —
rephrasing when a search comes back thin, running one search per clause of a multi-part
question — and only then writes the answer. Retrieval becomes part of the model's
reasoning rather than a fixed step in front of it.

Two phases, because they want different things from the model:
  1. the tool loop, where it may only search;
  2. the answer, streamed, with inline [[chunk_id|quote]] citation markers.

Markers are parsed out of the stream as they close, the quote is checked against the
passage it cites, and what the user sees is the document's own wording. See grounding.py.
"""

import asyncio
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import UUID

import asyncpg
from google.genai import types

from .config import settings
from .gemini import stream
from .grounding import locate
from .retrieval import Passage, format_passages, search

SEARCH_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="search_documents",
            description=(
                "Search the user's uploaded documents. Returns passages, each tagged with "
                "the chunk id you must cite it by. Call it again with different wording "
                "when a search comes back thin or off-target."
            ),
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "query": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "The search phrase. Use the wording the document is likely to "
                            "use, not necessarily the user's wording."
                        ),
                    )
                },
                required=["query"],
            ),
        )
    ]
)

SYSTEM = """You answer questions about a user's own document library, citing the documents.

You have one tool: search_documents. Use it before answering anything. Call it with no
preamble — do not write text and then call the tool in the same turn.

- Search with the document's likely wording, not the user's. Someone asking how to "get out
  of the contract" is looking for a clause that says "termination".
- If a search returns nothing useful, search again with different terms. A question with
  several parts needs several searches - one per part. You may search up to {rounds} times.

When you have what you need, write the answer. Use only the passages your searches returned.

After each sentence that asserts a fact from a passage, append a citation marker:

    [[<chunk id>|<exact quote>]]

The quote is copied character for character out of that passage. Never paraphrase inside a
marker, never merge two passages into one quote, and never cite a chunk id you were not given.

A passage about a different party, agreement or document than the question asks about is
not an answer to it. Quoting eight other vendors' contracts at someone asking about one
specific vendor is worse than saying nothing: every quote verifies, so nothing downstream
can catch it. Check that each passage is about the thing that was asked about.

If the passages do not contain the answer, say so plainly in one or two sentences and append
no markers at all. Do not fall back on general knowledge, and do not apologise at length."""

_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


class MarkerStream:
    """Separates visible prose from [[chunk_id|quote]] markers in a token stream.

    Tokens split wherever the model's decoder happens to break, so a marker routinely
    arrives in pieces. Text is released as soon as it is known not to be part of a marker.
    """

    OPEN = "[["
    CLOSE = "]]"

    def __init__(self) -> None:
        self._buf = ""
        self._in_marker = False

    def feed(self, text: str) -> list[tuple[str, str]]:
        self._buf += text
        return list(self._drain())

    def _drain(self):
        while self._buf:
            if self._in_marker:
                end = self._buf.find(self.CLOSE)
                if end == -1:
                    return
                yield ("cite", self._buf[:end])
                self._buf = self._buf[end + len(self.CLOSE) :]
                self._in_marker = False
                continue

            start = self._buf.find(self.OPEN)
            if start == -1:
                # A trailing "[" may be the first half of a marker — hold it back.
                keep = 1 if self._buf.endswith("[") else 0
                if len(self._buf) > keep:
                    yield ("text", self._buf[: len(self._buf) - keep])
                self._buf = self._buf[len(self._buf) - keep :]
                return
            if start:
                yield ("text", self._buf[:start])
            self._buf = self._buf[start + len(self.OPEN) :]
            self._in_marker = True

    def flush(self) -> list[tuple[str, str]]:
        """At end of stream, anything still held was never a marker. Show it as text."""
        if not self._buf and not self._in_marker:
            return []
        out = [("text", (self.OPEN if self._in_marker else "") + self._buf)]
        self._buf = ""
        self._in_marker = False
        return out


@dataclass
class Session:
    """What the agent retrieved, so citations can be checked against it afterwards."""

    passages: dict[str, Passage] = field(default_factory=dict)
    searches: list[dict] = field(default_factory=list)
    dropped: int = 0


def _parse_marker(raw: str) -> tuple[str, str]:
    chunk_id, _, quote = raw.partition("|")
    return chunk_id.strip(), quote.strip()


async def answer(
    conn: asyncpg.Connection,
    space_id: UUID,
    question: str,
    *,
    history: list[types.Content] | None = None,
    document_ids: list[UUID] | None = None,
) -> AsyncIterator[dict]:
    """Run the agent and yield SSE-shaped events. See PRD §7.5 for the event contract.

    `search` is emitted twice per search: once with `hits: null` the instant the model
    decides to look something up, so the UI can show *what* it is looking for while the
    query runs, and once more with the count when results are back.
    """
    started = time.monotonic()
    session = Session()
    contents: list[types.Content] = list(history or [])
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=question)]))

    markers = MarkerStream()
    emitted = ""
    citations: list[dict] = []
    saw_marker = False

    async def handle(events):
        nonlocal emitted, saw_marker
        for kind, payload in events:
            if kind == "text":
                emitted += payload
                yield {"type": "token", "text": payload}
                continue

            saw_marker = True
            chunk_id, quote = _parse_marker(payload)
            passage = session.passages.get(chunk_id)
            if passage is None:
                session.dropped += 1  # a chunk id the agent was never given
                continue
            span = locate(quote, passage.text)
            if span is None:
                session.dropped += 1  # the quote is not in the passage it claims
                continue
            exact = passage.text[span[0] : span[1]]

            citation = {
                "id": str(len(citations) + 1),
                "chunk_id": chunk_id,
                "document_id": str(passage.document_id),
                "document_name": passage.document_name,
                "page": passage.page_at(span[0]),
                "quote": exact,
                "context": passage.text,
                "sentence_index": max(0, len(_SENTENCE_END.findall(emitted)) - 1),
            }
            citations.append(citation)
            yield {"type": "citation", "citation": citation}

    async def run_search(call) -> types.Part:
        query = str((call.args or {}).get("query", "")).strip()
        hits = await search(conn, space_id, query, document_ids=document_ids) if query else []
        for passage in hits:
            session.passages[str(passage.chunk_id)] = passage
        session.searches.append({"query": query, "hits": len(hits)})
        return types.Part.from_function_response(
            name=call.name, response={"passages": format_passages(hits)}
        )

    # One streamed turn per round. A turn that calls the tool becomes a search and another
    # round; a turn that produces text *is* the answer, streamed as it arrives. This is what
    # removes the old "reply READY, then answer in a fresh call" round trip: the model's
    # decision that it has enough and its answer are the same tokens.
    system = SYSTEM.format(rounds=settings.max_search_rounds)
    for round_no in range(settings.max_search_rounds + 1):
        last_round = round_no == settings.max_search_rounds
        calls: list = []
        turn_parts: list[types.Part] = []

        async for chunk in stream(
            contents,
            system_instruction=system,
            tools=None if last_round else [SEARCH_TOOL],
        ):
            for part in (chunk.candidates[0].content.parts if chunk.candidates else []) or []:
                turn_parts.append(part)
                if part.function_call:
                    calls.append(part.function_call)
                elif part.text:
                    async for event in handle(markers.feed(part.text)):
                        yield event

        if not calls:
            break

        # The model chose to search. Anything it said first was preamble, not an answer;
        # the instruction forbids it, and it is short if it happens.
        contents.append(types.Content(role="model", parts=turn_parts))
        for call in calls:
            query = str((call.args or {}).get("query", "")).strip()
            yield {"type": "search", "query": query, "hits": None}
        # Independent searches run together — a two-part question costs one wait, not two.
        results = await asyncio.gather(*(run_search(call) for call in calls))
        for call, entry in zip(calls, session.searches[-len(calls):]):
            yield {"type": "search", "query": entry["query"], "hits": entry["hits"]}
        contents.append(types.Content(role="user", parts=list(results)))

    async for event in handle(markers.flush()):
        yield event

    yield {
        "type": "done",
        "answer": emitted.strip(),
        "citations": citations,
        "searches": session.searches,
        # The model tried to cite and every quote failed verification: the text is on
        # screen already, so flag it rather than pretend it was never said.
        "ungrounded": saw_marker and not citations,
        "dropped_citations": session.dropped,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }
