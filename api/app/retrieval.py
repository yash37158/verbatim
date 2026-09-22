"""Hybrid retrieval: dense vectors for meaning, Postgres FTS for exact strings, fused with RRF.

Pure vector search reliably misses the things these users search for most — invoice numbers,
statute references, product SKUs, defined terms. Pure keyword search misses every paraphrase.
Reciprocal Rank Fusion combines the two rankings without needing the two scores to be
commensurable, which they are not.
"""

from dataclasses import dataclass
from uuid import UUID

import asyncpg

from .config import settings
from .db import vec
from .gemini import embed_query

# One query, both arms, fused in the database: no round trip to fuse, and the planner
# keeps the tenant filter next to the index scan.
_SEARCH_SQL = """
with query as (
    -- websearch_to_tsquery ANDs every term, so a ten-word question demands all ten appear
    -- in one passage and matches nothing — the keyword arm goes silent for exactly the
    -- queries people actually type, and hybrid search quietly degrades to dense-only.
    -- Flipping the rendered operators to OR lets ts_rank_cd rank by how much of the
    -- question a passage covers. Rewriting the output rather than the input keeps
    -- websearch_to_tsquery's sanitising, and leaves phrase (<->) and negation (!) intact.
    select nullif(replace(websearch_to_tsquery('english', $5)::text, '&', '|'), '')::tsquery as tq
),
sem as (
    select id, row_number() over (order by dist) as rank
    from (
        select id, embedding <=> $3::vector as dist
        from chunks
        where space_id = $1
          and (cardinality($2::uuid[]) = 0 or document_id = any($2))
        order by embedding <=> $3::vector
        limit $4
    ) s
),
kw as (
    select id, row_number() over (order by score desc) as rank
    from (
        select c.id, ts_rank_cd(c.tsv, query.tq) as score
        from chunks c, query
        where query.tq is not null
          and c.space_id = $1
          and (cardinality($2::uuid[]) = 0 or c.document_id = any($2))
          and c.tsv @@ query.tq
        order by score desc
        limit $4
    ) k
),
fused as (
    select coalesce(sem.id, kw.id) as id,
           coalesce(1.0 / ($6 + sem.rank), 0) + coalesce(1.0 / ($6 + kw.rank), 0) as score,
           sem.rank as sem_rank, kw.rank as kw_rank
    from sem full outer join kw on sem.id = kw.id
)
select c.id, c.document_id, d.filename, c.page_start, c.page_end, c.text,
       c.char_start, d.page_offsets, f.score, f.sem_rank, f.kw_rank
from fused f
join chunks c on c.id = f.id
join documents d on d.id = c.document_id
order by f.score desc, c.id
limit $7
"""


@dataclass(frozen=True)
class Passage:
    chunk_id: UUID
    document_id: UUID
    document_name: str
    page_start: int | None
    page_end: int | None
    text: str
    char_start: int
    page_offsets: list[list[int | None]]
    score: float
    sem_rank: int | None
    kw_rank: int | None

    def page_at(self, offset_in_chunk: int) -> int | None:
        """The page a character of this chunk sits on.

        A chunk can straddle a page break, so the page a citation names comes from where
        the quote is, not where its chunk happens to start.
        """
        absolute = self.char_start + offset_in_chunk
        page = self.page_start
        for start, number in self.page_offsets:
            if start is not None and start <= absolute:
                page = number
            else:
                break
        return page

    @property
    def locator(self) -> str:
        if self.page_start is None:
            return self.document_name
        if self.page_end and self.page_end != self.page_start:
            return f"{self.document_name}, pp. {self.page_start}-{self.page_end}"
        return f"{self.document_name}, p. {self.page_start}"


async def search(
    conn: asyncpg.Connection,
    space_id: UUID,
    query: str,
    *,
    document_ids: list[UUID] | None = None,
    top_k: int | None = None,
) -> list[Passage]:
    """Retrieve passages for `query`, scoped to one Space the caller already owns."""
    rows = await conn.fetch(
        _SEARCH_SQL,
        space_id,
        document_ids or [],
        vec(await embed_query(query)),
        settings.retrieval_candidates,
        query,
        settings.rrf_k,
        top_k or settings.retrieval_top_k,
    )
    return [Passage(**dict(r)) for r in _renamed(rows)]


def _renamed(rows):
    for r in rows:
        d = dict(r)
        d["chunk_id"] = d.pop("id")
        d["document_name"] = d.pop("filename")
        d["score"] = float(d["score"])  # asyncpg returns numeric as Decimal, which will not serialise
        yield d


def format_passages(passages: list[Passage]) -> str:
    """Render passages for the model. The chunk id is the citation handle — the model
    quotes it back, and the server checks the quote against this exact text."""
    if not passages:
        return "No passages matched this query."
    return "\n\n".join(
        f"[{p.chunk_id}] ({p.locator})\n{p.text.strip()}" for p in passages
    )
