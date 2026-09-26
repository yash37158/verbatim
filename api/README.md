# Verbatim API

FastAPI · Postgres 16+ with pgvector · Gemini for embeddings and generation.

Agentic RAG: the model is given a search tool and decides for itself what to look for,
whether the results were good enough, and when to stop. Every quote it returns is checked
against the passage it cites before the user sees it.

## Run it

```bash
createdb verbatim && psql -d verbatim -f schema.sql
cp .env.example .env          # add GEMINI_API_KEY
uv sync
uv run python -m app.bootstrap you@example.com   # prints an API key
uv run uvicorn app.main:app --reload --port 8000
```

```bash
createdb verbatim_test && psql -d verbatim_test -f schema.sql   # once
uv run pytest          # 97 tests; needs the database, never touches the network
```

Tests run against `verbatim_test`, **not** the dev database. They drive ingestion by calling
`process_one()` directly, and a dev server pointed at the same database has its own worker
draining the same queue — the two race, and a suite that fails depending on whether a server
happens to be running is worse than no suite. Override with `TEST_DATABASE_URL`.

Unit tests roll back their transaction; the HTTP tests create a user per test and delete it,
and everything cascades.

## The pipeline

### 1 · Ingest — [`chunking.py`](app/chunking.py), [`ingest.py`](app/ingest.py)

`parse → chunk → index` first, `embed` second, and that ordering is the whole story.

Parsing and chunking a 60-page PDF takes about four seconds and needs no network. The
keyword index is a generated column, so the moment the chunks commit the document is
**fully searchable by exact wording** — status `ready`, usable, seconds after upload.
Embedding is slow and rate limited, so it runs as a separate backfill (`embed_pending`),
one batch per commit, and the semantic arm simply grows as vectors land. The UI shows
`ready · semantic 34/91` for a document in that state. A throttled embedding API no longer
holds a new upload hostage; it just means meaning-based search widens a little more slowly.

Jobs are claimed out of the `documents` table with `for update skip locked`. That table *is* the queue: no broker, no scheduler,
no second thing to operate, and a worker that dies mid-job releases its row when the lock
goes stale. `RUN_WORKER=false` on the API plus `python -m app.ingest` in its own process
is how this scales out.

Chunking is recursive: cut on paragraph breaks, fall back to lines, then sentences, then
words, then a hard cut. ~3,200 characters (~800 tokens) with 480 of overlap, so a clause
split across a boundary still appears whole in one of the two chunks.

Every chunk carries `char_start`/`char_end` into the document's flattened text and the
page range it spans. The invariant `full_text[char_start:char_end] == chunk.text` is
tested, because a citation is only as good as its offsets.

Failures are sorted into three kinds, because they need three different answers:

| Kind | Example | What happens |
|---|---|---|
| The file | scan with no text layer, over the page limit | `failed` immediately, never retried — retrying changes nothing |
| The provider | 429 quota, 503 overload | back to `queued` with a 15-minute `retry_after`, **and it does not spend an attempt** |
| Anything else | a bug, a disk error | three attempts, then `failed` |

The middle row matters more than it looks. A daily quota needs hours; the in-process
backoff covers about thirty seconds. Treating a quota window like a bad file wrote off
perfectly good uploads and left no way back except delete-and-re-upload. Provider messages
are also condensed before they are stored — Google returns a screenful of JSON, and it used
to land verbatim in the sidebar.

`POST /api/documents/{id}/retry` says *try this now* — it clears both a `failed` status and
a pending `retry_after`, and is surfaced as a Retry button. Refused only while the document
is actually being ingested.

**Embedding is resumable.** Chunks are written batch by batch, and a retry skips ordinals
already present. Without that, a 91-chunk document on a rate-limited key re-embeds from zero
every attempt, gets exactly as far as the last one, and never finishes. The same commit
renews the worker's claim, so a slow document is not re-claimed mid-flight.

**The wait comes from Google, not from us.** A 429 carries `retryDelay`, `quotaId` and
`quotaValue`; those set `retry_after` and the message. A per-window limit waits what the
server asks (plus a cushion); a `PerDay` quota waits 15 minutes regardless, because a daily
cap will not clear in the 38 seconds Google suggests. The UI renders the timestamp in the
viewer's timezone next to the progress banked so far:

> 22 sections indexed so far · continues 17:52 (2 min) · nothing for you to do

### 2 · Retrieve — [`retrieval.py`](app/retrieval.py)

Hybrid, in one SQL statement, and **never blocked by the embedding API**: if embedding the
query is refused (429, 503), search runs on the keyword arm alone rather than failing. A
rate limit on one provider is not a reason to give the user no answer.

- **dense** — `embedding <=> query` over an HNSW index, top 20
- **sparse** — `ts_rank_cd` over a GIN index on `tsvector`, top 20
- **fuse** — Reciprocal Rank Fusion, `1/(60 + rank)` summed across arms

Vector search alone reliably misses the things people actually search for — invoice
numbers, statute references, defined terms — because an embedding of `INV-90210` is not
meaningfully near the query `INV-90210`. Keyword search alone misses every paraphrase.
RRF combines the two rankings without needing their scores to be comparable, which they
are not. There is a test for each half and for the case where one arm rescues the other.

`websearch_to_tsquery` parses the user's raw text, so punctuation cannot produce a
malformed tsquery.

Embeddings are `gemini-embedding-001` truncated to 1536 dimensions (pgvector's HNSW caps
`vector` at 2000) and re-normalised, because Matryoshka-truncated output is not unit
length. Queries use `RETRIEVAL_QUERY`, chunks use `RETRIEVAL_DOCUMENT` — different
projections; mixing them costs recall.

### 3 · Answer — [`agent.py`](app/agent.py), [`grounding.py`](app/grounding.py)

One streamed turn per round. The model holds one tool, `search_documents(query)`, and each
turn either calls it — which becomes a search and another round — or produces text, which
*is* the answer, streamed as it arrives. Capped at four searches, after which the tool is
withheld and it must answer.

This used to be three calls: a turn that decides to search, a turn that says `READY`, and a
fresh call to write the answer. The `READY` turn was a full model call that produced nothing,
and on the free tier that was a third of the 26-second median. Now the decision that it has
enough and the answer are the same tokens. Two searches requested in one turn run
concurrently, so a two-part question costs one wait rather than two.

Search runs on a tenant-scoped connection and is logged, which is why the loop is driven
here rather than by the SDK's automatic function calling. The answer carries inline markers:

```
Either party may terminate on thirty days notice.[[<chunk_id>|thirty (30) days’ prior written notice]]
```

Markers are parsed out of the token stream as they close — tested against every one of
the 41 possible split points in a sample stream — so text reaches the user immediately
and citations arrive the moment each claim is grounded. A marker that never closes is
shown as literal text rather than swallowed.

Structured JSON output would have been the obvious alternative, but Gemini rejects a
response schema alongside tools, and a JSON field cannot be streamed without a partial
parser. Markers give streaming, quotes and one model call.

**The verification gate.** For each marker: is that chunk id one the agent actually
retrieved, and does the quote appear in it? Matching tolerates whitespace, smart quotes
and case, because a false negative silently deletes a true citation. What the user is
shown is sliced out of the document — so a model that types a straight apostrophe still
produces the document's curly one. Anything that fails is dropped.

If the model claimed sources and every one failed, the `done` event carries
`ungrounded: true`. The PRD said to replace such an answer with an abstention; once the
text is streamed that is no longer possible, and flagging it is more honest than
pretending it was never said.

## Models

Model IDs move faster than any docs. What this account actually had, on 2026-09-22:

- `gemini-2.5-pro` and `gemini-2.5-flash` appear in `models.list()` but return **404 for
  new users**. Listing is not availability — probe with a real call.
- Working: `gemini-3.8-flash`, `gemini-3.7-flash`, `gemini-3.6-flash`, `gemini-3.5-flash`,
  `gemini-3-flash-preview`, `gemini-3.1-flash-lite`.
- `gemini-3.1-pro-preview` exists but has no free-tier quota.
- Embeddings: `gemini-embedding-001` (also `gemini-embedding-2`, untested here).

**The free tier is the binding constraint**: 20 generate requests per day *per model*, and
one question costs 2-3 (a search round per tool call, plus the answer). That is roughly
seven questions per model per day. Transient 503s from demand spikes are common on top of
that; `stream()` retries up to five times before the first token and never after, since a
half-delivered answer cannot be resumed. A paid key removes both problems.

`GEMINI_MODEL` is the only place the ID lives. `/health` echoes it.

## Where an uploaded file goes

```
browser  ──multipart──▶  Next route handler (:3000)  ──▶  FastAPI (:8000)
                         adds the bearer token            sha256, dedupe
                                                               │
                        ┌──────────────────────────────────────┴───────────┐
                        ▼                                                  ▼
            api/storage/<ab>/<sha256>                     documents row, status='queued'
            the original bytes, content-addressed         (Postgres)
                        │
                        ▼  worker claims the row
            parse (pymupdf / python-docx, local)  ──▶  chunks
                        │
                        ▼  the only step that leaves the machine
            chunk text ──▶ Gemini embeddings ──▶ vectors
                        │
                        ▼
            chunks row: text + vector(1536) + page offsets  (Postgres)
```

- **Original bytes**: `api/storage/<first two hex>/<sha256>` on local disk. Content-addressed,
  so the same file uploaded to two Spaces is stored once.
- **Extracted text, embeddings, answers and citations**: Postgres (`/opt/homebrew/var/postgresql@18`
  on this machine).
- **What reaches Google**: chunk *text* at ingest, and the retrieved passages plus the
  question at query time. The file itself never leaves the machine — but its contents do,
  twice. Everything else is local.

Deleting a document removes its row, its chunks, and its bytes on disk — the last only once
no other Space references the same sha256. Deleting a *Space* is a soft delete (PRD FR-4,
30-day recovery), so its files stay until a hard delete, which is not implemented yet.

## Limits

Enforced:

| Limit | Value | Where | On breach |
|---|---|---|---|
| File size | 50 MB | upload, before anything is stored | `413`, immediately |
| Pages per file | 1,000 | after parse, before embedding | document marked `failed`, not retried |
| Upload body | 48 MB decoded | `Content-Length`, before the body is read | `413` |

Not enforced: 200 files per Space (PRD FR-5). Nothing stops a Space growing indefinitely.

Measured on this hardware, text-heavy PDFs at ~3,500 characters a page:

| Pages | Parse | Chunks | Embed requests | Ingest | Peak RSS |
|---|---|---|---|---|---|
| 100 | 0.4s | 200 | 13 | ~20s | 71 MB |
| 500 | 2.0s | 1,000 | 63 | ~95s | 71 MB |
| 1,000 | 4.3s | 2,000 | 125 | ~3 min | 77 MB |
| 2,000 | 8.5s | 4,000 | 250 | ~6 min | 91 MB |

Parsing and chunking are free and flat in memory; embedding is the entire cost. Roughly
two chunks per page, eleven chunks a second.

**Page count, not file size, is what matters.** 1,000 pages of pure text is about 1 MB —
the 50 MB cap only binds on image-heavy or scanned PDFs, which have few pages of text and
are rejected at parse anyway. A file can be well under 50 MB and far over the page limit.

**Requests are sized by tokens, not by chunk count.** Measured against this key: ~18k
tokens per embedding request succeeds, ~46k is refused. Batching by count sent 100 chunks
(~92k tokens) in one request and 429'd on anything past a toy document.

**The worker heartbeats its claim** between embedding batches. Without that, any document
taking longer than `STALE_AFTER` to embed is re-claimed by a second worker mid-flight and
ingested twice — which at eleven chunks a second means anything past ~3,000 pages.

Free tier changes all of this: the daily generation cap makes anything past a few hundred
pages impractical regardless of these numbers.

## Running into free-tier limits

Two levers, both free, both measured against this key.

### Model failover — [`gemini.py`](app/gemini.py)

Gemini quotas are **per model** (`GenerateRequestsPerDayPerProjectPerModel`). A chain of
five is therefore five separate pools, not one:

```
GEMINI_MODEL=gemini-3.1-flash-lite
GEMINI_FALLBACK_MODELS=["gemini-3.6-flash","gemini-3-flash-preview","gemini-3.7-flash","gemini-3.5-flash"]
```

On a 429 or 503 the next model is tried **immediately** rather than backing off — a refusal
is about one model's capacity, and waiting spends the delay *and* forgoes the alternative.
A model that refuses is put on a cooldown taken from Google's own `retryDelay`, so the next
question does not open by burning a request on a pool known to be empty. Only when the whole
chain has refused is it worth sleeping, and the user is told that specifically:

> All 5 models are rate limited or overloaded right now. The first frees up in about 12 min.

A 400 is never failed over — it will fail identically everywhere.

**Embeddings deliberately do not rotate.** Vectors from two embedding models occupy
different spaces; mixing them across a corpus destroys retrieval silently, with no error to
notice. The embedding model is a one-time choice, not a failover.

### Embedding pace — `Pace` in [`gemini.py`](app/gemini.py)

Sprinting into the rate limit costs a 30-second-plus penalty; pacing costs a couple of
seconds and never trips it. The gap between requests self-tunes — additive increase,
multiplicative decrease, the same shape as TCP congestion control and for the same reason:
the ceiling is unknown, differs per key, and overshooting is far more expensive than going
slightly slower.

### What none of this fixes

Request *count* per question is already near the floor: one generate per search round plus
one for the answer, so two to three. The only ways further down are fewer search rounds
(`max_search_rounds`, currently 4) at the cost of multi-part questions, or a paid key.

## Evals

Two things are measured, separately and on purpose. When an answer is wrong it is either
because the passage was never retrieved or because it was retrieved and the model still got
it wrong, and those need opposite fixes.

### Grounding, from production traffic

Every answer records `ungrounded` and `dropped_citations` on its message row, alongside the
model that produced it. No eval set and no API cost — it is measured on real questions:

```sql
select model,
       count(*)                                          as answers,
       round(100.0 * sum(dropped_citations) /
             nullif(sum(jsonb_array_length(citations)) + sum(dropped_citations), 0), 2)
                                                         as drop_rate_pct,
       round(100.0 * count(*) filter (where ungrounded) / count(*), 2) as ungrounded_pct,
       percentile_cont(0.5) within group (order by latency_ms)         as p50_ms
  from messages
 where role = 'assistant' and created_at > now() - interval '7 days'
 group by model order by answers desc;
```

PRD §9 targets: drop rate under 2%, grounding rate at or above 95%. A rising drop rate is
the early warning that the model has started drifting from its sources.

### A corpus worth measuring against — [`app/corpus.py`](app/corpus.py)

```bash
uv run python -m app.evals seed --user-email you@example.com --documents 12
```

Twelve synthetic master services agreements, twenty clauses each: ~156 chunks, which puts
top-8 at 5% of the corpus and clears the saturation line. Built for two properties that
pull against each other — every document shares a domain and register so distractors are
genuinely hard ("thirty (30) days" appears in all of them), while every *specific* fact —
notice period, liability cap, uptime figure — lives in exactly one document. That second
property is what makes the abstention eval valid.

### Retrieval — [`app/evals.py`](app/evals.py)

```bash
uv run python -m app.evals build --space <uuid> --limit 50 --out evals/contracts.json
uv run python -m app.evals run evals/contracts.json --k 8 --min-recall 0.8
```

`build` writes one case per chunk: a question the model paraphrases from the passage, plus
the sentence that answers it. Each case is validated before it is saved — a gold quote that
is not actually in the passage is discarded, so the set cannot contain an unpassable case.

**Gold is a verbatim snippet, never a chunk id.** Re-ingesting a document deletes its chunks
and inserts new ones with new ids, so an id-keyed eval set rots silently the first time
anyone re-chunks. A snippet survives re-chunking, re-embedding and every parameter change.

`run` costs one query embedding per case and no generation, so it is cheap to run on every
change. It exits non-zero below `--min-recall`, which is what makes it a CI gate. Results
land in `evals/results/` with a snapshot of the chunking and retrieval settings — two runs
are only comparable if those match.

It reports `recall@k` (was the answer in front of the model at all), `MRR` (how near the
top), and which arm found the hit — the last one being the direct answer to PRD open
question 3, whether hybrid retrieval earns its cost.

**Read the saturation warning.** If top-k returns more than a fifth of the corpus, recall is
near-guaranteed and the number means nothing. The 12-chunk demo corpus is far past that
line; a real measurement needs a few hundred chunks.

### Abstention — does it say "not in your documents" when that is true?

```bash
uv run python -m app.evals abstain evals/corpus.json --limit 10 --min-accuracy 0.9
```

The hard part of an abstention eval is knowing a question is genuinely unanswerable. Asking
a model to invent unanswerable questions gives you questions it *guesses* are unanswerable,
and you are back to needing a judge.

Instead each case is run with **its own source document withheld from scope**. The answer is
then provably absent, by construction, with nothing to verify and no judge required — and it
reuses the document-scoping that already exists and is tested. Correct behaviour is to cite
nothing and say so.

This is why the corpus keeps every specific fact in a single document. In a corpus of
near-identical contracts a sibling document would legitimately answer the question, and a
non-abstention would be scored as a failure when it was not one. Cases that answer anyway are
reported with what they cited, so a fabrication is distinguishable from that situation.

Unlike the retrieval eval this runs the full agent, so it costs roughly three model requests
per case. `--limit` exists for that reason.

### Baseline — 12 documents, 143 chunks, 13 cases, `gemini-3.1-flash-lite`

| Metric | Result | Threshold |
|---|---|---|
| recall@8 | 84.6% | 80% |
| MRR | 0.924 | — |
| abstention accuracy | 100% (6 cases) | 90% |

Both misses are notice-period questions: twelve contracts carry near-identical "X days
notice" clauses, and the vendor's name turns out to be a weak signal against that much
clause-text similarity. Real, but thirteen cases is not enough to tune term weighting on.

Six abstention cases is a small sample. 100% here does not mean 100%.

### What this already caught

The first run reported 100% recall and *zero* keyword-arm hits across all eight cases.
`websearch_to_tsquery` ANDs every term, so a ten-word question demanded all ten words appear
in one passage, matched nothing, and silently reduced hybrid search to dense-only — the
keyword arm was dead for exactly the queries people type. Fixed by flipping the rendered
operators to OR, which keeps `websearch_to_tsquery`'s sanitising and leaves phrase and
negation operators intact.

**Ambiguous questions cannot be scored by gold-chunk matching.** The first run against the
real corpus scored 61.5%, and every miss was a question like "what is the late payment
penalty?" — which has twelve equally correct answers in a library of twelve parallel
contracts. Retrieval was fine; the questions were not well-posed. The build prompt now
requires each question to name the party it concerns, and the same set scores 84.6%. (This
is a measurement constraint, not a product one: a real user asking an ambiguous question
deserves an answer, it just cannot be graded by exact match.)

**Answering about the wrong party.** Asked for Kestrel's warranty period with the Kestrel
contract withheld, the agent answered by citing eight *other* vendors' warranty clauses.
Every quote verified — nothing was fabricated — so the grounding gate could not catch it,
and in a contract tool attributing one company's terms to another is worse than a
hallucination. Only the abstention eval surfaced it. The answer prompt now states that a
passage about a different party is not an answer; abstention went from 83.3% to 100%.

## Deliberate deviations from the PRD

| PRD | Here | Why |
|---|---|---|
| FR-6 presigned upload to S3/R2 | multipart POST to content-addressed local disk | No object store to configure yet. Two call sites in `ingest.py` to swap. |
| §7.4 row-level security | tenancy enforced in the query layer | Every Space-owned read joins through `spaces.user_id`; four cross-tenant probes are tested. RLS is the second layer — add it before multi-user Spaces. |
| §7.2 Gemini Flash rerank | not built | PRD open question 3 said to measure RRF first. The agent re-querying covers some of the same ground. |
| §7.3 structured JSON output | inline citation markers | See above. |
| FR-18 replace ungrounded answers | flag them | Cannot unsend a streamed answer. |

Auth is a bearer token per user. Magic link and OAuth issue the same tokens later.

## Layout

```
schema.sql          tables, HNSW + GIN indexes, the queue's partial index
app/
  config.py         one settings object; every tunable lives here
  db.py             asyncpg pool, pgvector text encoding
  chunking.py       parse + recursive chunking with exact offsets
  gemini.py         the only module that talks to Google — batching, retries, normalising
  retrieval.py      hybrid search + RRF, in one statement
  grounding.py      quote verification
  agent.py          tool loop, marker stream, citation events
  ingest.py         the worker
  main.py           routes, auth, SSE
```

## What happens with a document you have not seen before

Verified by parsing locally (nothing uploaded) and, for DOCX, end to end through the API:

| Input | Result |
|---|---|
| Two-column PDF with running headers | Columns read in the right order, not interleaved |
| Table-heavy page | Parses; rows stay together while they fit one chunk |
| Single short page | One chunk, fine |
| DOCX | Ingests and is searchable — **but has no page numbers** |
| TXT / Markdown | Ingests and is searchable, also unpaginated |
| Non-English text | Ingests; dense search works, **keyword arm degrades** |
| A real PDF from real software | Parses, offsets round-trip |
| Scanned PDF | Rejected at parse with an actionable message |

Two caveats worth knowing before you rely on them:

**Only PDFs get page numbers.** A .docx has no pages — pagination is the renderer's
decision, and python-docx cannot know it. Citations from DOCX, TXT and Markdown say
"Unpaginated" and locate the quote by character offset instead. That is honest rather than
inventing a page, but it is a real downgrade in the source drawer.

**Non-English documents lose the keyword arm.** The `tsv` column is a generated column
hardcoded to `to_tsvector('english', ...)`. On French text that indexes French stopwords as
lexemes and stems by English rules, so `ts_rank_cd` ranks poorly. Gemini embeddings are
multilingual so semantic search still works and answers still come back — hybrid just
quietly becomes dense-only. Postgres ships a French config (30 are installed); the fix is to
detect language at ingest, store it per document, and use it in both the generated column
and the query. Not a one-liner, because the generated column has to change.

## Known gaps

- No rate limiting. A single user can saturate the embedding quota.
- Space deletion is soft only. There is no hard-delete path, so a GDPR erasure request
  cannot be fully served today — documents inside a deleted Space keep their rows and bytes.
- A 429 carrying a `retryDelay` (Google sends one) is retried on our own backoff schedule
  rather than the server's. When the server asks for 38s and we wait 15, we fail anyway.
- Abstention accuracy is still unmeasured — the eval set format has no unanswerable
  cases yet. That is the next metric, and the cheapest one left to add.
- Tables split across chunks still answer questions wrong (PRD open question 5).
- `sentence_index` is counted server-side with a regex and re-segmented client-side with
  `Intl.Segmenter`; they can disagree on abbreviations, which misplaces a chip by one
  sentence. A character offset would be exact.
