"""Retrieval eval: when the agent asks, does the right passage come back?

Scored on its own, deliberately. When an answer is wrong there are two very different
causes — the passage was never retrieved, or it was retrieved and the model still got it
wrong — and they need opposite fixes. Measuring only "was the answer good?" cannot tell
them apart, and you end up rewriting prompts to fix a chunking bug.

Gold is a verbatim snippet, never a chunk id. Re-ingesting a document deletes its chunks
and inserts new ones with new ids, so an id-keyed eval set silently rots the first time
anyone re-chunks. A snippet survives re-chunking, re-embedding and parameter changes, and
`locate()` matches it exactly as tolerantly as a citation.

    uv run python -m app.evals build --space <uuid> --out evals/contracts.json
    uv run python -m app.evals run evals/contracts.json --k 8 --min-recall 0.8

`run` costs one query embedding per case and no generation, so it is cheap to run often.
It exits non-zero below --min-recall, which is what makes it usable as a CI gate.
"""

import argparse
import asyncio
import hashlib
import json
import statistics
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from google.genai import types

from . import corpus, ingest
from .agent import answer
from .config import settings
from .db import acquire, disconnect
from .gemini import generate
from .grounding import locate
from .retrieval import search

SCHEMA_VERSION = 1

# Top-k above this share of the corpus means recall is near-guaranteed and the number
# cannot tell a good retriever from a bad one.
SATURATION_RATIO = 0.2

_CASE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "question": types.Schema(
            type=types.Type.STRING,
            description="A question with exactly one correct answer across a library of "
            "similar agreements. It must name the party or agreement it concerns, "
            "and must not reuse the passage's phrasing.",
        ),
        "quote": types.Schema(
            type=types.Type.STRING,
            description="The sentence from the passage that answers it, copied exactly.",
        ),
    },
    required=["question", "quote"],
)

_BUILD_SYSTEM = (
    "You are writing test cases for a document search engine. Given one passage and the "
    "file it came from, write a question it answers and copy out the sentence that answers "
    "it.\n\n"
    "The question must have exactly ONE correct answer across a library of similar "
    "documents. The corpus is full of near-identical agreements, so a question like 'what "
    "is the late payment penalty?' has a dozen equally valid answers and is unusable as a "
    "test case. Name the party or agreement the question is about, taking the name from the "
    "passage or the filename.\n\n"
    "Do not reuse the passage's phrasing otherwise — retrieval is only interesting when the "
    "user's words differ from the document's."
)


@dataclass
class Case:
    id: str
    question: str
    gold_quote: str  # must appear verbatim in whichever chunk answers the question
    document: str


@dataclass
class EvalSet:
    space_id: str
    created_at: str
    schema_version: int = SCHEMA_VERSION
    cases: list[Case] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "EvalSet":
        raw = json.loads(path.read_text())
        version = raw.get("schema_version")
        if version != SCHEMA_VERSION:
            raise SystemExit(f"{path}: eval set is schema v{version}, this build reads v{SCHEMA_VERSION}")
        return cls(
            space_id=raw["space_id"],
            created_at=raw["created_at"],
            schema_version=version,
            cases=[Case(**c) for c in raw["cases"]],
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2) + "\n")


# --------------------------------------------------------------------------- scoring


@dataclass
class Outcome:
    """One case, run. `rank` is None when no retrieved passage contained the gold text."""

    case_id: str
    question: str
    document: str
    rank: int | None
    dense_rank: int | None  # the hit's rank in the dense arm, None if that arm missed it
    sparse_rank: int | None  # ditto for the keyword arm
    error: str | None = None


def score(outcomes: list[Outcome], k: int, corpus: int | None = None) -> dict:
    """Pure, so every number here is testable without a database or a network."""
    graded = [o for o in outcomes if o.error is None]
    hits = [o for o in graded if o.rank is not None]
    total = len(graded)

    return {
        "cases": len(outcomes),
        "graded": total,
        "errors": len(outcomes) - total,
        "k": k,
        # The headline: how often the answer was in front of the model at all.
        "recall_at_k": round(len(hits) / total, 4) if total else 0.0,
        # Where in the list. Rank 1 and rank 8 both count for recall, but the model
        # reads the top of the list far more carefully.
        "mrr": round(statistics.fmean([1 / o.rank for o in hits]) if hits else 0.0, 4),
        "median_rank": statistics.median([o.rank for o in hits]) if hits else None,
        # Whether hybrid retrieval is earning its keep — PRD open question 3.
        "rescued_by_keyword": sum(1 for o in hits if o.dense_rank is None),
        "rescued_by_semantic": sum(1 for o in hits if o.sparse_rank is None),
        "found_by_both": sum(1 for o in hits if o.dense_rank and o.sparse_rank),
        "misses": [
            {"case_id": o.case_id, "question": o.question, "document": o.document}
            for o in graded
            if o.rank is None
        ],
        "failed": [{"case_id": o.case_id, "error": o.error} for o in outcomes if o.error],
        "corpus_chunks": corpus,
        # Below this the metric stops discriminating: top-k returns so much of the corpus
        # that recall is near-guaranteed whether retrieval is good or not.
        "saturated": bool(corpus) and k / corpus > SATURATION_RATIO,
    }


def config_snapshot() -> dict:
    """Recorded with every result. Two runs are only comparable if these match."""
    return {
        "embed_model": settings.gemini_embed_model,
        "embed_dims": settings.embed_dims,
        "chunk_chars": settings.chunk_chars,
        "chunk_overlap_chars": settings.chunk_overlap_chars,
        "chunk_max_pages": settings.chunk_max_pages,
        "retrieval_candidates": settings.retrieval_candidates,
        "rrf_k": settings.rrf_k,
    }


# --------------------------------------------------------------------------- seed


async def seed(email: str, name: str, documents: int) -> None:
    """Generate a contract corpus, ingest it, and report whether it is big enough to measure."""
    async with acquire() as conn:
        user_id = await conn.fetchval("select id from users where email = $1", email.lower())
        if user_id is None:
            raise SystemExit(f"No user {email}. Create one: python -m app.bootstrap {email}")
        space_id = await conn.fetchval(
            "insert into spaces (user_id, name, description) values ($1, $2, $3) returning id",
            user_id, name, "Synthetic contracts for retrieval and abstention evals.",
        )
        for doc in corpus.build(documents):
            digest = hashlib.sha256(doc.pdf).hexdigest()
            ingest.store(digest, doc.pdf)
            await conn.execute(
                """insert into documents (space_id, filename, mime_type, size_bytes,
                                          sha256, storage_key)
                   values ($1, $2, 'application/pdf', $3, $4, $5)""",
                space_id, doc.filename, len(doc.pdf), digest, digest,
            )
        print(f"space {space_id}: {documents} documents queued", file=sys.stderr)

    # Drained here rather than left to the worker, so the command finishes when the corpus
    # is actually searchable. Embedding is rate limited; the retries inside it are the slow part.
    done = 0
    while await ingest.process_one():
        done += 1
        print(f"  ingested {done}/{documents}", file=sys.stderr)

    async with acquire() as conn:
        chunks = await conn.fetchval("select count(*) from chunks where space_id = $1", space_id)
        failed = await conn.fetch(
            "select filename, error from documents where space_id = $1 and status = 'failed'",
            space_id,
        )
    for row in failed:
        print(f"  FAILED {row['filename']}: {row['error']}", file=sys.stderr)

    share = settings.retrieval_top_k / chunks if chunks else 1.0
    print(f"\n{chunks} chunks — top-{settings.retrieval_top_k} is {share:.1%} of the corpus"
          f" ({'usable' if share <= SATURATION_RATIO else 'still saturated'})", file=sys.stderr)
    print(f"space_id: {space_id}", file=sys.stderr)


# --------------------------------------------------------------------------- build


async def build(space_id: UUID, limit: int, out: Path) -> EvalSet:
    async with acquire() as conn:
        chunks = await conn.fetch(
            '''select c.id, c.text, d.filename
                 from chunks c join documents d on d.id = c.document_id
                where c.space_id = $1
                -- Hashed order, not document order: `--limit 12` on a corpus ordered by
                -- filename would sample twelve chunks of one contract and call it coverage.
                -- md5 of the id is deterministic, so the same limit yields the same sample.
                order by md5(c.id::text)
                limit $2''',
            space_id, limit,
        )
    if not chunks:
        raise SystemExit(f"No indexed chunks in space {space_id}")

    cases: list[Case] = []
    for i, chunk in enumerate(chunks, 1):
        try:
            prompt = f"File: {chunk['filename']}\n\nPassage:\n{chunk['text']}"
            response = await generate(
                [types.Content(role="user", parts=[types.Part.from_text(text=prompt)])],
                system_instruction=_BUILD_SYSTEM,
                response_schema=_CASE_SCHEMA,
                temperature=0.4,
            )
            payload = json.loads(response.text)
        except Exception as e:  # noqa: BLE001 — one bad chunk must not lose the whole set
            print(f"  [{i}/{len(chunks)}] skipped ({type(e).__name__}: {str(e)[:70]})", file=sys.stderr)
            continue

        quote = (payload.get("quote") or "").strip()
        question = (payload.get("question") or "").strip()
        # Self-validating: a gold quote the model invented would make the case unpassable.
        if not question or not quote or locate(quote, chunk["text"]) is None:
            print(f"  [{i}/{len(chunks)}] rejected — quote is not in the passage", file=sys.stderr)
            continue

        cases.append(Case(f"c{len(cases) + 1:03d}", question, quote, chunk["filename"]))
        print(f"  [{i}/{len(chunks)}] {question[:72]}", file=sys.stderr)

    eval_set = EvalSet(str(space_id), datetime.now(UTC).isoformat(), SCHEMA_VERSION, cases)
    eval_set.save(out)
    print(f"\n{len(cases)} cases from {len(chunks)} chunks -> {out}", file=sys.stderr)
    return eval_set


# --------------------------------------------------------------------------- run


async def _run_case(conn, space_id: UUID, case: Case, k: int) -> Outcome:
    try:
        hits = await search(conn, space_id, case.question, top_k=k)
    except Exception as e:  # noqa: BLE001 — record and keep going; one failure is not a verdict
        return Outcome(case.id, case.question, case.document, None, None, None, f"{type(e).__name__}: {e}")

    for rank, passage in enumerate(hits, 1):
        if locate(case.gold_quote, passage.text) is not None:
            return Outcome(case.id, case.question, case.document, rank,
                           passage.sem_rank, passage.kw_rank)
    return Outcome(case.id, case.question, case.document, None, None, None)


async def run(path: Path, k: int, concurrency: int) -> dict:
    eval_set = EvalSet.load(path)
    space_id = UUID(eval_set.space_id)
    gate = asyncio.Semaphore(concurrency)

    async with acquire() as conn:
        corpus = await conn.fetchval("select count(*) from chunks where space_id = $1", space_id)

    async def one(case: Case) -> Outcome:
        async with gate, acquire() as conn:  # a connection per case; the pool bounds it
            return await _run_case(conn, space_id, case, k)

    outcomes = await asyncio.gather(*(one(c) for c in eval_set.cases))
    outcomes = sorted(outcomes, key=lambda o: o.case_id)  # deterministic report order

    return {
        "eval_set": str(path),
        "space_id": eval_set.space_id,
        "run_at": datetime.now(UTC).isoformat(),
        "config": config_snapshot(),
        "metrics": score(list(outcomes), k, corpus),
        "outcomes": [asdict(o) for o in outcomes],
    }


# --------------------------------------------------------------------------- abstention


@dataclass
class AbstentionOutcome:
    """One case run with its source document removed from scope, so the answer is
    provably absent. Correct behaviour is to cite nothing and say so."""

    case_id: str
    question: str
    withheld: str
    abstained: bool
    cited: list[str] = field(default_factory=list)
    answer: str = ""
    error: str | None = None


def score_abstention(outcomes: list["AbstentionOutcome"]) -> dict:
    graded = [o for o in outcomes if o.error is None]
    correct = [o for o in graded if o.abstained]
    return {
        "cases": len(outcomes),
        "graded": len(graded),
        "errors": len(outcomes) - len(graded),
        "abstention_accuracy": round(len(correct) / len(graded), 4) if graded else 0.0,
        "answered_anyway": [
            {"case_id": o.case_id, "question": o.question, "withheld": o.withheld,
             "cited": o.cited, "answer": o.answer[:200]}
            for o in graded if not o.abstained
        ],
        "failed": [{"case_id": o.case_id, "error": o.error} for o in outcomes if o.error],
    }


async def _abstain_case(conn, space_id: UUID, case: Case, scope: dict[str, UUID]) -> AbstentionOutcome:
    """Run the real agent with the answering document withheld."""
    withheld = case.document
    others = [doc_id for name, doc_id in scope.items() if name != withheld]
    if not others:
        return AbstentionOutcome(case.id, case.question, withheld, False,
                                 error="nothing left in scope once the source is withheld")
    try:
        done = None
        async for event in answer(conn, space_id, case.question, document_ids=others):
            if event["type"] == "done":
                done = event
    except Exception as e:  # noqa: BLE001 — an outage is not evidence about abstention
        return AbstentionOutcome(case.id, case.question, withheld, False,
                                 error=f"{type(e).__name__}: {str(e)[:120]}")

    cited = [f"{c['document_name']} p.{c['page']}" for c in done["citations"]]
    return AbstentionOutcome(case.id, case.question, withheld, not cited, cited, done["answer"])


async def run_abstention(path: Path, limit: int) -> dict:
    eval_set = EvalSet.load(path)
    space_id = UUID(eval_set.space_id)
    async with acquire() as conn:
        scope = {r["filename"]: r["id"] for r in await conn.fetch(
            "select id, filename from documents where space_id = $1 and status = 'ready'",
            space_id)}

    cases = eval_set.cases[:limit] if limit else eval_set.cases
    outcomes: list[AbstentionOutcome] = []
    for i, case in enumerate(cases, 1):
        async with acquire() as conn:  # sequential: the agent is the rate-limited part
            outcomes.append(await _abstain_case(conn, space_id, case, scope))
        print(f"  [{i}/{len(cases)}] {'abstained' if outcomes[-1].abstained else 'ANSWERED'}"
              f"  {case.question[:58]}", file=sys.stderr)

    return {
        "eval_set": str(path),
        "space_id": eval_set.space_id,
        "run_at": datetime.now(UTC).isoformat(),
        "config": config_snapshot() | {"model": settings.gemini_model},
        "metrics": score_abstention(outcomes),
        "outcomes": [asdict(o) for o in outcomes],
    }


def report_abstention(result: dict, minimum: float) -> bool:
    m = result["metrics"]
    print(f"\n  abstention eval · {result['eval_set']}")
    print(f"  {m['graded']} cases graded"
          + (f", {m['errors']} errored" if m["errors"] else ""))
    print("  each question's own document was withheld, so the answer is provably absent\n")
    print(f"    abstention accuracy   {m['abstention_accuracy']:.1%}"
          "   (said so instead of reaching)")

    if m["answered_anyway"]:
        print(f"\n    {len(m['answered_anyway'])} answered anyway:")
        for bad in m["answered_anyway"]:
            print(f"      {bad['case_id']}  {bad['question'][:60]}")
            print(f"            withheld {bad['withheld']}, cited {bad['cited']}")
    for failed in m["failed"]:
        print(f"      {failed['case_id']}  ERROR {failed['error'][:70]}")

    passed = m["abstention_accuracy"] >= minimum
    print(f"\n  {'PASS' if passed else 'FAIL'} — {m['abstention_accuracy']:.1%} "
          f"vs threshold {minimum:.0%}\n")
    return passed


def report(result: dict, min_recall: float) -> bool:
    m = result["metrics"]
    print(f"\n  retrieval eval · {result['eval_set']}")
    print(f"  {m['graded']} cases graded, k={m['k']}"
          + (f", {m['errors']} errored" if m["errors"] else ""))
    print(f"\n    recall@{m['k']}      {m['recall_at_k']:.1%}   (gold passage was retrieved at all)")
    print(f"    MRR            {m['mrr']:.3f}   (1.0 = always ranked first)")
    print(f"    median rank    {m['median_rank']}")
    print("\n    where the hit came from")
    print(f"      both arms            {m['found_by_both']}")
    print(f"      keyword only         {m['rescued_by_keyword']}   <- dense search missed these")
    print(f"      semantic only        {m['rescued_by_semantic']}   <- keyword search missed these")

    if m.get("saturated"):
        share = m["k"] / m["corpus_chunks"]
        print(f"\n  ! top-{m['k']} returns {share:.0%} of this {m['corpus_chunks']}-chunk corpus.")
        print("    Recall is near-guaranteed at this size — the number cannot tell a good")
        print("    retriever from a bad one. Index more documents before trusting it.")

    if m["misses"]:
        print(f"\n    {len(m['misses'])} not retrieved:")
        for miss in m["misses"]:
            print(f"      {miss['case_id']}  {miss['question'][:68]}")
    for failed in m["failed"]:
        print(f"      {failed['case_id']}  ERROR {failed['error'][:68]}")

    passed = m["recall_at_k"] >= min_recall
    print(f"\n  {'PASS' if passed else 'FAIL'} — recall@{m['k']} {m['recall_at_k']:.1%} "
          f"vs threshold {min_recall:.0%}\n")
    return passed


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.evals", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="generate an eval set from an indexed Space")
    b.add_argument("--space", required=True, type=UUID)
    b.add_argument("--limit", type=int, default=50, help="chunks to sample (one case each)")
    b.add_argument("--out", required=True, type=Path)

    sd = sub.add_parser("seed", help="generate and ingest a contract corpus to evaluate against")
    sd.add_argument("--user-email", required=True)
    sd.add_argument("--name", default="Eval Corpus")
    sd.add_argument("--documents", type=int, default=12)

    a = sub.add_parser("abstain", help="does it say 'not in your documents' when that is true?")
    a.add_argument("path", type=Path)
    a.add_argument("--limit", type=int, default=0, help="0 runs every case")
    a.add_argument("--min-accuracy", type=float, default=0.9)
    a.add_argument("--results-dir", type=Path, default=Path("evals/results"))

    r = sub.add_parser("run", help="score retrieval against an eval set")
    r.add_argument("path", type=Path)
    r.add_argument("--k", type=int, default=settings.retrieval_top_k)
    r.add_argument("--min-recall", type=float, default=0.8)
    r.add_argument("--concurrency", type=int, default=4)
    r.add_argument("--results-dir", type=Path, default=Path("evals/results"))

    args = parser.parse_args()

    async def go() -> int:
        try:
            if args.command == "seed":
                await seed(args.user_email, args.name, args.documents)
                return 0
            if args.command == "build":
                await build(args.space, args.limit, args.out)
                return 0
            if args.command == "abstain":
                cases = args.limit or len(EvalSet.load(args.path).cases)
                print(f"  {cases} cases, each a full agent run — roughly "
                      f"{cases * 3} model requests\n", file=sys.stderr)
                result = await run_abstention(args.path, args.limit)
                args.results_dir.mkdir(parents=True, exist_ok=True)
                stamp = result["run_at"].replace(":", "").replace("-", "")[:15]
                out = args.results_dir / f"{args.path.stem}-abstain-{stamp}.json"
                out.write_text(json.dumps(result, indent=2) + "\n")
                ok = report_abstention(result, args.min_accuracy)
                print(f"  saved {out}\n")
                return 0 if ok else 1
            result = await run(args.path, args.k, args.concurrency)
            args.results_dir.mkdir(parents=True, exist_ok=True)
            stamp = result["run_at"].replace(":", "").replace("-", "")[:15]
            out = args.results_dir / f"{args.path.stem}-{stamp}.json"
            out.write_text(json.dumps(result, indent=2) + "\n")
            ok = report(result, args.min_recall)
            print(f"  saved {out}\n")
            return 0 if ok else 1
        finally:
            await disconnect()

    sys.exit(asyncio.run(go()))


if __name__ == "__main__":
    main()
