from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://localhost:5432/verbatim"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    # Quotas are per model ("GenerateRequestsPerDayPerProjectPerModel"), so a chain is
    # free extra capacity: when one is throttled or overloaded, the next is a different
    # pool entirely. Generation only — see gemini.py for why embeddings must never rotate.
    gemini_fallback_models: list[str] = [
        "gemini-3.6-flash",
        "gemini-3-flash-preview",
        "gemini-3.7-flash",
        "gemini-3.5-flash",
    ]
    gemini_embed_model: str = "gemini-embedding-001"

    # pgvector's HNSW index caps the `vector` type at 2000 dims, so we truncate
    # gemini-embedding-001 with Matryoshka rather than reach for halfvec. See PRD §7.1.
    embed_dims: int = 1536
    embed_batch: int = 100  # hard cap on items per request
    # The real limit is tokens per request, not items. Batching by count sends a huge
    # request for a document with large chunks and wastes round trips on one with small
    # ones. Measured: ~18k tokens per request succeeds on the free tier, ~46k is refused.
    embed_batch_tokens: int = 15_000
    # Sprinting into the rate limit costs a 30s+ penalty; pacing costs a couple of seconds
    # and never trips it. The gap self-tunes between these bounds — see Pace in gemini.py.
    embed_min_gap: float = 0.5
    embed_max_gap: float = 45.0

    chunk_chars: int = 3200  # ~800 tokens at the usual 4 chars/token for English prose
    chunk_overlap_chars: int = 480
    # A citation naming eight pages is no citation. Caps how far one chunk can range
    # on sparse documents (decks, forms) where the character limit never bites.
    chunk_max_pages: int = 3

    retrieval_candidates: int = 20  # per arm, before fusion
    retrieval_top_k: int = 8  # passages handed to the model
    rrf_k: int = 60

    max_search_rounds: int = 4  # cap on the agent's tool loop

    # ponytail: local disk. Swap for S3/R2 by replacing the two calls in ingest.py.
    storage_dir: Path = Path("./storage")
    run_worker: bool = True

    max_upload_bytes: int = 50 * 1024 * 1024
    # PRD FR-5. Checked after parsing, before embedding — page count is not knowable from
    # the bytes, and the point is to refuse in a second rather than spend ten minutes and
    # real money discovering the file was never reasonable.
    max_pages: int = 1000
    cors_origins: list[str] = ["http://localhost:3000"]

    # Google sign-in. The redirect URI registered with Google is
    # {public_base_url}/api/backend/auth/google/callback
    google_client_id: str = ""
    google_client_secret: str = ""
    public_base_url: str = "http://localhost:3000"
    session_days: int = 30
    # Browsers refuse a Secure cookie over plain http, so this is off for local development
    # and must be on anywhere real.
    cookie_secure: bool = False


settings = Settings()
