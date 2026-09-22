import json
from contextlib import asynccontextmanager

import asyncpg

from .config import settings

_pool: asyncpg.Pool | None = None


async def connect() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=2,
            max_size=20,
            init=_init_connection,
        )
    return _pool


async def _init_connection(conn: asyncpg.Connection) -> None:
    # asyncpg hands back jsonb as text otherwise.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def acquire():
    pool = await connect()
    async with pool.acquire() as conn:
        yield conn


def vec(values: list[float]) -> str:
    """pgvector's text input format. Cast the parameter with ::vector at the call site.

    ponytail: the binary protocol (via the `pgvector` package) is ~3x smaller on the wire.
    Worth adding when ingestion throughput, not embedding latency, becomes the bottleneck.
    """
    return "[" + ",".join(repr(float(v)) for v in values) + "]"
