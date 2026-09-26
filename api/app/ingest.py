"""Ingestion: parse -> chunk -> embed -> index.

The documents table doubles as the job queue. `for update skip locked` is the whole
concurrency story: N workers can run against one Postgres without a broker, a scheduler
or a second piece of infrastructure to operate, and a worker that dies mid-job releases
its row when its lock expires.
"""

import asyncio
import logging
from datetime import timedelta
from pathlib import Path

import asyncpg

from .chunking import UnreadableDocument, chunk_pages, extract, page_offsets
from .config import settings
from .db import acquire, vec
from .gemini import batches, embed_batch, friendly_error, is_transient, parse_rate_limit

log = logging.getLogger("verbatim.ingest")

MAX_ATTEMPTS = 3
STALE_AFTER = "10 minutes"
RETRY_WINDOW = timedelta(minutes=15)  # how long a rate-limited document waits
IDLE_POLL_SECONDS = 2.0

_CLAIM = f"""
update documents
   set status = 'parsing', locked_at = now(), attempts = attempts + 1
 where id = (
     select id from documents
      where (status = 'queued' and (retry_after is null or retry_after <= now()))
         or (status in ('parsing', 'embedding') and locked_at < now() - interval '{STALE_AFTER}')
      order by created_at
        for update skip locked
      limit 1
 )
returning id, space_id, filename, mime_type, storage_key, attempts
"""

_INSERT_CHUNK = """
insert into chunks (document_id, space_id, ordinal, page_start, page_end,
                    char_start, char_end, text)
values ($1, $2, $3, $4, $5, $6, $7, $8)
"""


def storage_path(storage_key: str) -> Path:
    # ponytail: content-addressed local disk. To move to S3/R2, replace this and the two
    # call sites (write in main.upload_document, read in _process).
    return settings.storage_dir / storage_key[:2] / storage_key


def store(storage_key: str, data: bytes) -> None:
    path = storage_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def discard(storage_key: str) -> None:
    """Remove stored bytes. Already-missing is success — the point is that it is gone."""
    storage_path(storage_key).unlink(missing_ok=True)


async def process_one() -> bool:
    """Claim one queued document, parse it, and make it searchable. Returns False when the
    queue is empty.

    Chunks are written immediately with no embedding. The keyword index is a generated
    column, so the document is fully searchable by exact wording the moment this commits —
    seconds after upload. Semantic search arrives as `embed_pending` fills the vectors in
    behind, one batch at a time, and the two arms simply fuse whatever is there.
    """
    async with acquire() as conn:
        job = await conn.fetchrow(_CLAIM)
        if job is None:
            return False

        try:
            data = storage_path(job["storage_key"]).read_bytes()
            pages = extract(data, job["filename"], job["mime_type"])
            if len(pages) > settings.max_pages:
                raise UnreadableDocument(
                    f"{len(pages)} pages exceeds the {settings.max_pages}-page limit. "
                    "Split the document and upload the parts."
                )
            _, chunks = chunk_pages(pages)
            if not chunks:
                raise UnreadableDocument("This document contains no text.")

            async with conn.transaction():
                # A retry after a parse-time crash starts clean; embeddings are never here
                # yet, so nothing of value is lost.
                await conn.execute("delete from chunks where document_id = $1", job["id"])
                await conn.executemany(
                    _INSERT_CHUNK,
                    [
                        (job["id"], job["space_id"], c.ordinal, c.page_start, c.page_end,
                         c.char_start, c.char_end, c.text)
                        for c in chunks
                    ],
                )
                await conn.execute(
                    """update documents
                          set status = 'ready', error = null, locked_at = null, retry_after = null,
                              page_offsets = $3,
                              page_count = coalesce(page_count, $2)
                        where id = $1""",
                    job["id"],
                    max((c.page_end or 0) for c in chunks) or None,
                    [[start, number] for start, number in page_offsets(pages)],
                )
            log.info("%s: searchable, %d chunks; embedding in the background",
                     job["filename"], len(chunks))

        except UnreadableDocument as e:
            await _fail(conn, job["id"], str(e))  # the file itself is the problem
        except Exception as e:  # noqa: BLE001 — parse-time failures are rare and worth a retry
            log.exception("ingest failed for %s", job["filename"])
            if job["attempts"] >= MAX_ATTEMPTS:
                await _fail(conn, job["id"],
                            f"Failed after {MAX_ATTEMPTS} attempts. {friendly_error(e)}")
            else:
                await conn.execute(
                    "update documents set status = 'queued', locked_at = null where id = $1",
                    job["id"],
                )
        return True


# Which ready document still has vectors to fill in. Oldest first, and never one that is
# waiting out a rate-limit window.
_NEXT_TO_EMBED = """
select d.id, d.filename
  from documents d
 where d.status = 'ready'
   and (d.retry_after is null or d.retry_after <= now())
   and exists (select 1 from chunks c where c.document_id = d.id and c.embedding is null)
 order by d.created_at
 limit 1
"""


async def embed_pending() -> bool:
    """Fill in embeddings for one batch of one document. Returns False when nothing is waiting.

    Separate from `process_one` on purpose: parsing is fast and local, embedding is slow
    and rate limited. Coupling them would hold a document hostage to the embedding API.
    Each batch is its own commit, so progress survives a crash and a throttled key just
    means the semantic arm grows more slowly.
    """
    async with acquire() as conn:
        doc = await conn.fetchrow(_NEXT_TO_EMBED)
        if doc is None:
            return False

        pending = await conn.fetch(
            "select id, text from chunks where document_id = $1 and embedding is null "
            "order by ordinal limit $2",
            doc["id"], settings.embed_batch,
        )
        group = batches([r["text"] for r in pending], settings.embed_batch_tokens,
                        settings.embed_batch)[0]
        rows = pending[: len(group)]

        try:
            vectors = await embed_batch(group)
        except Exception as e:  # noqa: BLE001
            if is_transient(e):
                limit = parse_rate_limit(e)
                await conn.execute(
                    "update documents set retry_after = now() + $2, error = $3 where id = $1",
                    doc["id"], limit.delay if limit else RETRY_WINDOW, friendly_error(e),
                )
                return True
            # Anything else: leave the chunks unembedded and log. Keyword search still works,
            # and the next pass will try again.
            log.exception("embedding failed for %s", doc["filename"])
            await conn.execute(
                "update documents set retry_after = now() + $2, error = $3 where id = $1",
                doc["id"], RETRY_WINDOW, friendly_error(e),
            )
            return True

        async with conn.transaction():
            await conn.executemany(
                "update chunks set embedding = $2::vector where id = $1",
                [(r["id"], vec(v)) for r, v in zip(rows, vectors)],
            )
            remaining = await conn.fetchval(
                "select count(*) from chunks where document_id = $1 and embedding is null",
                doc["id"],
            )
            if remaining == 0:
                await conn.execute(
                    "update documents set error = null, retry_after = null where id = $1",
                    doc["id"],
                )
                log.info("%s: fully embedded", doc["filename"])
        return True


async def _fail(conn: asyncpg.Connection, document_id, message: str) -> None:
    await conn.execute(
        "update documents set status = 'failed', error = $2, locked_at = null where id = $1",
        document_id, message,
    )


async def run_worker(stop: asyncio.Event) -> None:
    """Drain the queue, then idle. ponytail: polling. LISTEN/NOTIFY removes the 2s of
    idle latency, at the cost of a dedicated connection and reconnect handling."""
    while not stop.is_set():
        try:
            # Parsing first: it is fast, local, and makes a document searchable. Then fill
            # in vectors. A throttled embedding API never blocks a new upload from landing.
            if await process_one() or await embed_pending():
                continue
        except Exception:  # noqa: BLE001 — a worker must not die on a bad row
            log.exception("worker iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=IDLE_POLL_SECONDS)
        except TimeoutError:
            pass


if __name__ == "__main__":  # scale out: run this in its own process, RUN_WORKER=false on the API
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker(asyncio.Event()))
