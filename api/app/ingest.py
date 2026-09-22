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
                    char_start, char_end, text, embedding)
values ($1, $2, $3, $4, $5, $6, $7, $8, $9::vector)
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
    """Claim and ingest a single document. Returns False when the queue is empty."""
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

            await conn.execute("update documents set status = 'embedding' where id = $1", job["id"])

            # Resume rather than restart. Chunking is deterministic, so ordinals are stable
            # across attempts: anything already embedded stays embedded. Without this a long
            # document on a rate-limited key re-embeds from zero every retry and can never
            # finish, because each attempt gets no further than the last.
            already = {
                r["ordinal"]
                for r in await conn.fetch(
                    "select ordinal from chunks where document_id = $1", job["id"]
                )
            }
            pending = [c for c in chunks if c.ordinal not in already]
            if already:
                log.info("%s: resuming, %d/%d chunks already embedded",
                         job["filename"], len(already), len(chunks))

            groups = batches([c.text for c in pending], settings.embed_batch_tokens,
                             settings.embed_batch)
            at = 0
            for n, group in enumerate(groups, 1):
                vectors = await embed_batch(group)
                batch = pending[at : at + len(group)]
                at += len(group)
                async with conn.transaction():
                    await conn.executemany(
                        _INSERT_CHUNK,
                        [
                            (job["id"], job["space_id"], c.ordinal, c.page_start, c.page_end,
                             c.char_start, c.char_end, c.text, vec(v))
                            for c, v in zip(batch, vectors)
                        ],
                    )
                    # Committing the batch also renews the claim, so a slow document is not
                    # re-claimed by a second worker part-way through.
                    await conn.execute(
                        "update documents set locked_at = now() where id = $1", job["id"]
                    )
                if len(groups) > 4 and n % 5 == 0:
                    log.info("%s: %d/%d batches", job["filename"], n, len(groups))

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
            log.info("ingested %s (%d chunks)", job["filename"], len(chunks))

        except UnreadableDocument as e:
            # The document itself is the problem. Retrying changes nothing.
            await _fail(conn, job["id"], str(e))  # the file itself is the problem
        except Exception as e:  # noqa: BLE001 — anything else may be transient
            log.exception("ingest failed for %s", job["filename"])
            if is_transient(e):
                # Not the document's fault. Wait out the window and take another run at it,
                # and do not spend one of its attempts doing so.
                await conn.execute(
                    """update documents
                          set status = 'queued', locked_at = null, attempts = attempts - 1,
                              retry_after = now() + $2, error = $3
                        where id = $1""",
                    job["id"], RETRY_WINDOW, friendly_error(e),
                )
            elif job["attempts"] >= MAX_ATTEMPTS:
                await _fail(conn, job["id"],
                            f"Failed after {MAX_ATTEMPTS} attempts. {friendly_error(e)}")
            else:
                await conn.execute(
                    "update documents set status = 'queued', locked_at = null where id = $1",
                    job["id"],
                )
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
            if await process_one():
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
