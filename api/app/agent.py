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

import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from uuid import UUID

import asyncpg
from google.genai import types

from .config import settings
from .gemini import generate, stream
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

SEARCH_SYSTEM = """You are researching a question inside a user's own document library.

You have one tool: search_documents. Use it before answering anything.

- Search with the document's likely wording, not the user's. Someone asking how to "get out
  of the contract" is looking for a clause that says "termination".
- If a search returns nothing useful, search again with different terms.
- A question with several parts needs several searches - one per part.
- You may search up to {rounds} times in total.

When you have what you need, or you are satisfied the documents do not cover the question,
reply with exactly: READY

Do not write the answer yet."""

ANSWER_SYSTEM = """Answer using only the passages returned by your searches.

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

ANSWER_PROMPT = "Now write the answer, with citation markers, following the rules you were given."

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
    """Run the agent and yield SSE-shaped events. See PRD §7.5 for the event contract."""
    started = time.monotonic()
    session = Session()
    contents: list[types.Content] = list(history or [])
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=question)]))

    # Phase 1 — the model searches until it is satisfied.
    for _ in range(settings.max_search_rounds):
        response = await generate(
            contents,
            system_instruction=SEARCH_SYSTEM.format(rounds=settings.max_search_rounds),
            tools=[SEARCH_TOOL],
        )
        calls = response.function_calls or []
        if not calls:
            break

        contents.append(response.candidates[0].content)
        results: list[types.Part] = []
        for call in calls:
            query = str((call.args or {}).get("query", "")).strip()
            hits = await search(conn, space_id, query, document_ids=document_ids) if query else []
            for passage in hits:
                session.passages[str(passage.chunk_id)] = passage
            session.searches.append({"query": query, "hits": len(hits)})
            yield {"type": "search", "query": query, "hits": len(hits)}
            results.append(
                types.Part.from_function_response(
                    name=call.name, response={"passages": format_passages(hits)}
                )
            )
        contents.append(types.Content(role="user", parts=results))

    # Phase 2 — the answer, streamed, with markers resolved as they close.
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=ANSWER_PROMPT)]))

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

    async for part in stream(contents, system_instruction=ANSWER_SYSTEM):
        async for event in handle(markers.feed(part.text or "")):
            yield event
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
