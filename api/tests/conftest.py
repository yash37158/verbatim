import os
import uuid

import asyncpg
import pytest
import pytest_asyncio

from app.config import settings
from app.db import _init_connection

# A separate database, not the dev one. The API tests drive ingestion by calling
# process_one() directly; a dev server running against the same database has its own
# worker draining the same queue, and the two race. Tests that fail depending on whether
# a server happens to be up are worse than no tests.
DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://localhost:5432/verbatim_test")
settings.database_url = DSN  # before any pool is created


@pytest_asyncio.fixture(scope="session", autouse=True)
async def require_test_database():
    try:
        conn = await asyncpg.connect(DSN)
    except Exception as e:  # noqa: BLE001
        pytest.exit(
            f"Cannot reach the test database at {DSN} ({e}).\n"
            "  createdb verbatim_test && psql -d verbatim_test -f schema.sql",
            returncode=1,
        )
    missing = await conn.fetchval(
        "select count(*) = 0 from information_schema.tables "
        "where table_schema = 'public' and table_name = 'chunks'"
    )
    await conn.close()
    if missing:
        pytest.exit(f"{DSN} has no schema. Run: psql -d verbatim_test -f schema.sql",
                    returncode=1)


@pytest_asyncio.fixture
async def conn():
    """A connection inside a transaction that is always rolled back — tests share one
    database and never see each other's rows."""
    c = await asyncpg.connect(DSN)
    await _init_connection(c)  # same codecs the pool installs, so tests see production types
    tr = c.transaction()
    await tr.start()
    try:
        yield c
    finally:
        await tr.rollback()
        await c.close()


@pytest_asyncio.fixture
async def space(conn):
    """A user with one Space, plus a second user's Space to prove isolation."""
    async def make(email: str) -> tuple[uuid.UUID, uuid.UUID]:
        user_id = await conn.fetchval(
            "insert into users (email, api_key) values ($1, $2) returning id",
            email, uuid.uuid4().hex,
        )
        space_id = await conn.fetchval(
            "insert into spaces (user_id, name) values ($1, 'Test Space') returning id", user_id
        )
        return user_id, space_id

    mine = await make(f"me-{uuid.uuid4().hex}@example.com")
    theirs = await make(f"other-{uuid.uuid4().hex}@example.com")
    return {"mine": mine[1], "theirs": theirs[1], "user_id": mine[0]}


@pytest_asyncio.fixture
def add_document(conn):
    async def make(space_id, filename="MSA.pdf", pages=48):
        return await conn.fetchval(
            """insert into documents (space_id, filename, mime_type, size_bytes, page_count,
                                      sha256, storage_key, status)
               values ($1, $2, 'application/pdf', 1000, $3, $4, 'k', 'ready') returning id""",
            space_id, filename, pages, uuid.uuid4().hex * 2,
        )
    return make
