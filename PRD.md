# Verbatim — Product Requirements Document

**Ask your documents. Get answers you can verify.**

| | |
|---|---|
| **Version** | 0.1 (draft) |
| **Date** | 2026-09-22 |
| **Owner** | Yash Sharma |
| **Status** | In review |

---

## 1. Problem

Long documents are slow to search manually. `Ctrl+F` finds strings, not answers — it fails
the moment the reader's words differ from the author's. Meanwhile, general-purpose chatbots
answer fluently about documents they have never read, and a confident wrong answer about a
contract, a policy, or a clinical protocol is worse than no answer at all.

The gap is **verifiability**. A user reading an AI answer about their own document has no
cheap way to check it, so they either re-read the source (losing the time the tool promised
to save) or trust it blind (accepting risk they cannot quantify).

## 2. Solution

Verbatim is a hosted workspace where a user uploads documents into **Spaces** and chats with
them. Every factual sentence in an answer carries an inline citation that resolves, in one
click, to the exact quoted passage and page in the source file.

Under the hood: retrieval-augmented generation. Documents are parsed, chunked, and embedded
into pgvector; a question is embedded and matched against those chunks by hybrid semantic +
keyword search; only retrieved passages are placed in the model's context. The model is
instructed to answer from those passages alone, and **the server verifies every quote it
returns actually appears in the cited chunk before the answer reaches the user.**

That verification step is the product. Retrieval alone reduces hallucination; verification
makes groundedness a property of the system rather than a hope about the model.

## 3. Goals & non-goals

### Goals (v1)

| # | Goal | Success looks like |
|---|---|---|
| G1 | Upload PDF/TXT/DOCX/MD and ask questions within 60s of signup | p50 time-to-first-answer < 90s from landing |
| G2 | Every factual claim is citable to a page and passage | ≥ 95% of answer sentences carry a verified citation |
| G3 | Honest refusal when the corpus does not contain the answer | ≥ 90% correct abstention on held-out unanswerable questions |
| G4 | Organise documents into personal Spaces and query across a Space | Users create ≥ 2 Spaces in week 1 (median) |
| G5 | Answers feel fast | p50 first token < 2.5s, p95 complete answer < 12s |

### Non-goals (v1)

- **Teams, sharing, and permissions.** Spaces are personal. Multi-user Spaces are v2.
- **Document editing or annotation.** Verbatim reads; it does not write back to source files.
- **Agentic actions.** No tool use, no web browsing, no external integrations.
- **Real-time collaboration.** No live cursors, no co-editing.
- **Self-hosting.** Cloud only in v1.
- **Mobile apps.** Responsive web only.

## 4. Users

**Primary — the Analyst.** Consultants, lawyers, policy staff, grad students. Works through
50–500 page documents under deadline. Needs a defensible answer they can quote in their own
output. Will not tolerate an answer they cannot trace.

**Secondary — the Operator.** Support leads, ops managers, compliance staff. Owns a stable
corpus (handbooks, runbooks, SOPs) and answers the same questions repeatedly. Values a
persistent Space more than one-off upload.

**Anti-persona — the Casual Summariser.** Wants a TL;DR of one article. Free ChatGPT
already serves them. Not worth optimising for.

## 5. Core flows

### 5.1 Onboarding → first answer

1. Sign up (email magic link, or Google OAuth).
2. Land on Spaces. An empty state offers **New Space** with a one-field name prompt.
3. Drop files into the Space. Upload begins immediately; each file shows a live status
   (`queued → parsing → embedding → ready`).
4. The chat input unlocks as soon as the **first** document reaches `ready` — the user does
   not wait for the whole batch.
5. Ask a question. The answer streams in; citation chips appear inline as sentences complete.
6. Click a chip. The source drawer opens to the quoted passage with the surrounding context
   and page number.

### 5.2 Returning user

Spaces list, ordered by last activity, each showing document count and last question asked.
Opening a Space restores the most recent conversation. New conversations are explicit
(**New chat**), so a Space accumulates a durable question history rather than one endless thread.

### 5.3 Failure flows

| Situation | Behaviour |
|---|---|
| Answer not in corpus | "I couldn't find this in your documents." + the closest passages found, labelled as *not an answer* |
| Scanned PDF, no text layer | Flag at upload: "This PDF has no extractable text." Offer OCR (v1.1) or reject cleanly |
| Model returns an unverifiable quote | Citation stripped, sentence marked *unverified*, event logged for review |
| Upload over size/page limit | Rejected at the client with the actual limit stated |

## 6. Functional requirements

### 6.1 Spaces
- **FR-1** Create, rename, delete a Space. Delete cascades to documents, chunks, conversations.
- **FR-2** A Space has a name, optional description, and a document set.
- **FR-3** Queries are scoped to exactly one Space. No cross-Space retrieval in v1.
- **FR-4** Soft-delete with 30-day recovery; hard-delete on request (GDPR erasure).

### 6.2 Documents
- **FR-5** Accept PDF, TXT, MD, DOCX. Max 50 MB and 1,000 pages per file; 200 files per Space.
- **FR-6** Upload direct to object storage via presigned URL — file bytes never transit the API server.
- **FR-7** Ingestion runs async. Status is observable per document and streamed to the UI.
- **FR-8** Deduplicate by SHA-256; re-uploading an identical file is a no-op with a notice.
- **FR-9** Deleting a document removes its chunks and embeddings within the same transaction.

### 6.3 Chat
- **FR-10** Answers stream token-by-token (SSE).
- **FR-11** Multi-turn within a conversation; prior turns condition retrieval via query rewriting.
- **FR-12** Every answer returns a `citations[]` array; each citation resolves to `document_id`,
  `page`, `char_span`, and the verbatim `quote`.
- **FR-13** Conversations are persisted, listable, and renamable. Auto-titled from the first question.
- **FR-14** Users can filter retrieval to a subset of documents within the Space.

### 6.4 Grounding & citations
- **FR-15** The generation prompt receives retrieved passages only — never the full document,
  never the model's prior knowledge as an authorised source.
- **FR-16** The model must return structured output: `{answer_markdown, citations[]}`.
- **FR-17** **Quote verification:** for each citation, the server normalises whitespace and
  asserts `quote` is a substring of the cited chunk. Failures are dropped, not shown.
- **FR-18** If zero citations survive verification and the answer is not an abstention, the
  answer is replaced with an abstention and the event is logged as a grounding failure.
- **FR-19** Citation chips render inline at the end of the sentence they support.

## 7. Architecture

```
Next.js (Vercel)                FastAPI (Fly/Render)            Postgres 16 + pgvector
┌──────────────┐  REST/SSE     ┌────────────────────┐          ┌─────────────────────┐
│ App Router   │ ────────────▶ │ /spaces /documents │ ───────▶ │ users, spaces,      │
│ React + TW   │ ◀──────────── │ /chat (SSE)        │          │ documents, chunks,  │
│ server comps │               │ /ingest (worker)   │          │ conversations, msgs │
└──────────────┘               └────────────────────┘          └─────────────────────┘
       │                              │        │                         ▲
       │ presigned PUT                │        │ embed / generate        │ HNSW cosine
       ▼                              ▼        ▼                         │
  ┌─────────┐                  ┌───────────┐  ┌──────────────┐          │
  │   S3    │ ────────────────▶│ ingestion │  │ Gemini API   │──────────┘
  │ /R2     │   parse on pull  │  worker   │  │ embed + gen  │
  └─────────┘                  └───────────┘  └──────────────┘
```

### 7.1 Ingestion pipeline

| Stage | Detail |
|---|---|
| **Parse** | PDF via `pymupdf` (fast, keeps page + bounding-box offsets). DOCX via `python-docx`. TXT/MD read directly. Reject PDFs whose extracted text is < 100 chars/page — almost always a scan. |
| **Chunk** | Recursive split on structure (heading → paragraph → sentence), target **800 tokens, 120-token overlap**. Never cross a page boundary without recording both pages. Each chunk persists `document_id, page_start, page_end, char_start, char_end, text`. |
| **Embed** | Gemini `gemini-embedding-001`, `task_type=RETRIEVAL_DOCUMENT`, **1536 dims** via Matryoshka truncation. Batched 100 chunks/request with exponential backoff. |
| **Index** | `pgvector` HNSW, `vector_cosine_ops`, `m=16, ef_construction=64`. Plus a `tsvector` GIN index on chunk text for the keyword arm. |

> **Why 1536 and not 3072:** pgvector's HNSW index caps the `vector` type at 2,000 dimensions.
> 3072-dim embeddings require `halfvec` (up to 4,000) and the extra precision does not measurably
> improve recall@8 on prose. 1536 keeps the simple path and halves storage.

### 7.2 Retrieval

Hybrid, because pure vector search reliably misses exact identifiers — invoice numbers,
statute references, product SKUs — that users search for constantly.

1. Embed the query with `task_type=RETRIEVAL_QUERY`.
2. **Semantic arm:** top-20 by cosine distance, scoped `WHERE space_id = $1`.
3. **Keyword arm:** top-20 by `ts_rank_cd` over the same scope.
4. **Fuse** with Reciprocal Rank Fusion (`k=60`) into a single ranking.
5. **Rerank** the top 20 with Gemini Flash scoring each passage 0–10 for relevance to the question.
6. Pass the **top 8** into the generation context, each tagged with its `chunk_id`.

Multi-turn: before step 1, rewrite the question into a standalone query using the last 4 turns
(Gemini Flash, cached). "What about the second one?" is unretrievable; the rewrite makes it a query.

### 7.3 Generation

- **Model:** Gemini 2.5 Pro for answers, Gemini 2.5 Flash for rewriting, reranking, titling.
  Model IDs are pinned in `.env`, never hardcoded — verify against Google's current model list
  before each release.
- **Structured output:** `response_mime_type: application/json` with a response schema of
  `{answer_markdown: string, citations: [{chunk_id, quote, sentence_index}]}`.
- **System instruction:** answer only from the numbered passages; quote exactly; if the passages
  do not contain the answer, say so and do not speculate.
- **Post-processing (the verification gate):** normalise whitespace on both sides, assert each
  `quote` is a substring of chunk `chunk_id`. Drop failures. Log the drop rate as a health metric.

### 7.4 Data model

```sql
create extension if not exists vector;

create table users (
  id uuid primary key default gen_random_uuid(),
  email citext unique not null,
  created_at timestamptz not null default now()
);

create table spaces (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references users on delete cascade,
  name text not null,
  description text,
  created_at timestamptz not null default now(),
  deleted_at timestamptz
);

create table documents (
  id uuid primary key default gen_random_uuid(),
  space_id uuid not null references spaces on delete cascade,
  filename text not null,
  mime_type text not null,
  size_bytes bigint not null,
  page_count int,
  sha256 char(64) not null,
  status text not null default 'queued',      -- queued|parsing|embedding|ready|failed
  error text,
  storage_key text not null,
  created_at timestamptz not null default now(),
  unique (space_id, sha256)
);

create table chunks (
  id uuid primary key default gen_random_uuid(),
  document_id uuid not null references documents on delete cascade,
  space_id uuid not null references spaces on delete cascade,   -- denormalised: scopes the index
  ordinal int not null,
  page_start int, page_end int,
  char_start int not null, char_end int not null,
  text text not null,
  embedding vector(1536) not null,
  tsv tsvector generated always as (to_tsvector('english', text)) stored
);

create index on chunks using hnsw (embedding vector_cosine_ops) with (m = 16, ef_construction = 64);
create index on chunks using gin (tsv);
create index on chunks (space_id);

create table conversations (
  id uuid primary key default gen_random_uuid(),
  space_id uuid not null references spaces on delete cascade,
  title text,
  created_at timestamptz not null default now()
);

create table messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references conversations on delete cascade,
  role text not null,                          -- user|assistant
  content text not null,
  citations jsonb not null default '[]',       -- [{chunk_id, document_id, page, quote}]
  tokens_in int, tokens_out int, latency_ms int,
  created_at timestamptz not null default now()
);
```

Row-level security is enabled on every table, keyed on `user_id`. Retrieval queries filter by
`space_id` **before** the vector scan — a partial index per tenant is not needed at v1 scale,
but the `WHERE` clause placement is non-negotiable: it is the multi-tenancy boundary.

### 7.5 API contract

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/spaces` | List spaces with `document_count`, `last_activity_at` |
| `POST` | `/api/spaces` | `{name, description?}` |
| `PATCH`/`DELETE` | `/api/spaces/{id}` | Rename / soft-delete |
| `GET` | `/api/spaces/{id}/documents` | Includes `status`, `page_count`, `error` |
| `POST` | `/api/spaces/{id}/documents` | Returns `{document_id, upload_url}` — presigned PUT |
| `POST` | `/api/documents/{id}/complete` | Client signals upload done; enqueues ingestion |
| `DELETE` | `/api/documents/{id}` | Cascades to chunks |
| `GET` | `/api/spaces/{id}/conversations` | |
| `POST` | `/api/conversations/{id}/messages` | **SSE.** Events: `token`, `citation`, `done`, `error` |
| `GET` | `/api/chunks/{id}` | Full chunk text + neighbours, for the source drawer |

**SSE event shapes**

```jsonc
{ "type": "token",    "text": "The termination clause " }
{ "type": "citation", "id": "c_7", "chunk_id": "...", "document_id": "...",
                      "document_name": "MSA-2025.pdf", "page": 14,
                      "quote": "Either party may terminate on 30 days written notice." }
{ "type": "done",     "message_id": "...", "latency_ms": 3412 }
```

Citations arrive as their own events rather than inside the token stream, so the client can
render a chip the instant a claim is grounded instead of waiting for the answer to finish.

## 8. Non-functional requirements

| Area | Requirement |
|---|---|
| **Latency** | p50 first token < 2.5s; p95 full answer < 12s; ingestion < 30s for a 100-page PDF |
| **Throughput** | 50 concurrent chats, 20 concurrent ingestions per instance |
| **Availability** | 99.5% monthly; ingestion may degrade without taking chat down |
| **Cost** | < $0.04 per answer at p50; < $0.01 per 100 pages ingested |
| **Limits** | 50 MB / 1,000 pages per file; 200 files per Space; 20 Spaces per user (free tier: 2 Spaces, 20 files) |
| **Security** | TLS everywhere; encrypted at rest; RLS on every table; presigned URLs expire in 15 min |
| **Privacy** | Documents are never used for model training. Deletion is real and cascades. Data residency: single region (us-east) at v1 |
| **Accessibility** | WCAG 2.1 AA. Citation chips are real buttons, keyboard-reachable, with accessible names |

## 9. Metrics

**North star:** verified-cited answers per active user per week.

| Metric | Target |
|---|---|
| Grounding rate (sentences with a verified citation) | ≥ 95% |
| Quote verification drop rate | < 2% |
| Correct abstention on unanswerable questions | ≥ 90% |
| Citation click-through | ≥ 30% of answers (proxy for trust being exercised) |
| Time to first answer, signup → answer | p50 < 90s |
| Week-4 retention | ≥ 35% |

An eval set of 200 question/answer/citation triples over 20 documents gates every release.
Retrieval is measured separately (recall@8) from generation, because a grounding failure is
almost always a retrieval failure wearing a generation costume.

## 10. Milestones

| Phase | Scope |
|---|---|
| **M0 — PRD + UI** | This document. Next.js app with all three screens running against mocked data and the API contract above. *(current)* |
| **M1 — Backend spine** | FastAPI, Postgres + pgvector, auth, Spaces and Documents CRUD, presigned upload |
| **M2 — Ingestion** | Parse → chunk → embed → index worker; live status to the UI |
| **M3 — RAG** | Hybrid retrieval, rerank, generation, quote verification, SSE streaming |
| **M4 — Eval + polish** | Eval harness, grounding dashboard, rate limits, billing |
| **M5 — Beta** | 50 invited users, instrument everything in §9 |

## 11. Open questions

1. **OCR.** Scanned PDFs are a large share of real-world legal and medical corpora. Rejecting
   them cleanly is v1; is OCR (Gemini vision, or Tesseract) a v1.1 or a v2?
2. **Cross-Space search.** Users will ask for it. Does it dilute the Space metaphor, or is a
   "search all Spaces" mode the natural v2?
3. **Reranking cost.** A Flash rerank call per query adds latency and spend. Measure whether
   RRF alone hits recall@8 before shipping the rerank stage.
4. **Free tier shape.** Document-count cap or question-count cap? The former is easier to
   reason about; the latter maps to actual cost.
5. **Table and figure handling.** Chunking prose is solved; a table split across chunks answers
   questions wrong. Do we detect and keep tables atomic in v1?
