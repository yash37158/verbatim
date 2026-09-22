"""Thin async wrappers over google-genai: batched embeddings and one generate call.

Everything that talks to Google lives here, so retries, batching and model IDs have
exactly one home.
"""

import asyncio
import logging
import math
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

from google.genai import Client, types
from google.genai.errors import APIError, ClientError, ServerError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter

from .config import settings

log = logging.getLogger("verbatim.gemini")

_client: Client | None = None


def client() -> Client:
    global _client
    if _client is None:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        _client = Client(api_key=settings.gemini_api_key)
    return _client


# Google states the quota it refused on and how long to wait. Backing off on our own
# schedule instead means waiting the wrong amount and telling the user nothing.
_RETRY_DELAY = re.compile(r"'retryDelay':\s*'(\d+)")
_QUOTA_ID = re.compile(r"'quotaId':\s*'([^']+)'")
_QUOTA_VALUE = re.compile(r"'quotaValue':\s*'(\d+)'")

MIN_RETRY = timedelta(seconds=30)
DAILY_RETRY = timedelta(minutes=15)  # a daily cap will not clear in 38 seconds
MAX_RETRY = timedelta(hours=1)


@dataclass(frozen=True)
class RateLimit:
    delay: timedelta
    daily: bool
    limit: str | None
    reason: str


def parse_rate_limit(exc: BaseException) -> RateLimit | None:
    """Pull the quota details out of a 429, or None if this is not one."""
    if not isinstance(exc, APIError) or getattr(exc, "code", None) != 429:
        return None
    text = str(exc)
    quota_id = m.group(1) if (m := _QUOTA_ID.search(text)) else None
    limit = m.group(1) if (m := _QUOTA_VALUE.search(text)) else None
    daily = bool(quota_id and "PerDay" in quota_id)

    if daily:
        # Google sends a short retryDelay even for a daily cap. Honour the longer of the
        # two rather than hammering a window that cannot have moved.
        delay = DAILY_RETRY
        reason = f"Daily free-tier limit reached ({limit} requests)." if limit else \
                 "Daily free-tier limit reached."
    else:
        seconds = int(m.group(1)) if (m := _RETRY_DELAY.search(text)) else 60
        delay = timedelta(seconds=seconds + 5)  # a cushion, so the retry is not a second early
        reason = f"Rate limited ({limit} per window)." if limit else "Rate limited."

    return RateLimit(min(max(delay, MIN_RETRY), MAX_RETRY), daily, limit, reason)


def friendly_error(exc: BaseException) -> str:
    """What a user sees. The stack trace and the provider's JSON go to the log."""
    if isinstance(exc, AllModelsBusy):
        free_at = exc.free_at
        when = ""
        if free_at is not None:
            minutes = max(1, round((free_at - time.monotonic()) / 60))
            when = f" The first frees up in about {minutes} min."
        return (f"All {len(exc.tried)} models are rate limited or overloaded right now.{when}"
                " A paid API key removes the limits.")
    if isinstance(exc, APIError):
        code = getattr(exc, "code", None)
        if code == 429:
            limit = parse_rate_limit(exc)
            return limit.reason if limit else "Rate limited by the model provider."
        if code == 503:
            return "The model is briefly overloaded."
        if code == 404:
            return f"Model {settings.gemini_model} is not available to this API key."
        return f"The model returned an error ({code})."
    return f"{type(exc).__name__}: {str(exc).splitlines()[0][:160]}"


def is_transient(exc: BaseException) -> bool:
    """Worth trying again later — a quota window or an overloaded model, not a bad file."""
    return _transient(exc)


def _transient(exc: BaseException) -> bool:
    """Retry rate limits and server faults. Never retry a 400 — it will fail identically."""
    if isinstance(exc, ServerError):
        return True
    if isinstance(exc, ClientError):
        return exc.status == 429 or getattr(exc, "code", None) == 429
    return False


_retry = retry(
    retry=retry_if_exception(_transient),
    wait=wait_exponential_jitter(initial=1, max=30),
    stop=stop_after_attempt(5),
    reraise=True,
)


class Pace:
    """Spread requests out instead of sprinting into the rate limit.

    Additive increase, multiplicative decrease — the same shape as TCP congestion control,
    for the same reason: the ceiling is unknown, differs per key, and the penalty for
    overshooting (a 30s-plus backoff) dwarfs the cost of going slightly slower.
    """

    def __init__(self) -> None:
        self._gap = settings.embed_min_gap
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = asyncio.get_running_loop().time()
            sleep_for = self._last + self._gap - now
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            self._last = asyncio.get_running_loop().time()

    def eased(self) -> None:
        """A success: creep back towards full speed."""
        self._gap = max(settings.embed_min_gap, self._gap * 0.9)

    def throttled(self) -> None:
        """A 429: back off hard, because the next one costs far more than this does."""
        self._gap = min(settings.embed_max_gap, max(self._gap * 2, 2.0))

    @property
    def gap(self) -> float:
        return self._gap


pace = Pace()

# A model that just refused us is skipped until its window has plausibly passed, so the
# next question does not open by burning a request on a pool we know is empty.
_cooling: dict[str, float] = {}


class AllModelsBusy(Exception):
    """Every model in the chain refused. Reporting the last one's error alone reads as a
    blip, when the real situation is that there is no capacity left anywhere."""

    def __init__(self, tried: list[str], last: BaseException) -> None:
        self.tried = tried
        self.last = last
        super().__init__(f"all {len(tried)} models unavailable: {last}")

    @property
    def free_at(self) -> float | None:
        """Loop time when the first model becomes usable again."""
        waits = [_cooling[m] for m in self.tried if m in _cooling]
        return min(waits) if waits else None


def model_chain() -> list[str]:
    """Generation models in preference order, throttled ones moved to the back."""
    chain = [settings.gemini_model, *settings.gemini_fallback_models]
    now = time.monotonic()
    ready = [m for m in chain if _cooling.get(m, 0.0) <= now]
    # If every model is cooling, try them all anyway — a stale estimate is not a reason
    # to refuse a request the provider might well serve.
    return ready or chain


def _cool(model: str, exc: BaseException) -> None:
    limit = parse_rate_limit(exc)
    seconds = limit.delay.total_seconds() if limit else 60.0
    _cooling[model] = time.monotonic() + seconds
    log.warning("%s unavailable for ~%.0fs (%s)", model, seconds, friendly_error(exc))


def _unit(values: list[float]) -> list[float]:
    """Normalise to unit length.

    gemini-embedding-001 returns normalised vectors at its native 3072 dimensions, but
    Matryoshka-truncated outputs are not normalised. Cosine distance would cope either
    way; normalising here keeps the vectors valid for inner-product search too, which is
    the cheap upgrade if HNSW ever needs to get faster.
    """
    norm = math.sqrt(sum(v * v for v in values))
    return [v / norm for v in values] if norm else values


@_retry
async def embed_batch(texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT") -> list[list[float]]:
    """Embed one request's worth of text.

    Deliberately does NOT fall back to another model. Vectors from two embedding models
    live in different spaces; mixing them across a corpus silently destroys retrieval,
    and there is no error to notice. The model here is a one-time choice, not a failover.
    """
    await pace.wait()
    try:
        result = await _embed_once(texts, task_type)
    except APIError as e:
        if _transient(e):
            pace.throttled()
        raise
    pace.eased()
    return result


async def _embed_once(texts: list[str], task_type: str) -> list[list[float]]:
    resp = await client().aio.models.embed_content(
        model=settings.gemini_embed_model,
        contents=texts,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=settings.embed_dims,
        ),
    )
    return [_unit(e.values) for e in resp.embeddings]


def batches(texts: list[str], max_tokens: int, max_items: int) -> list[list[str]]:
    """Group texts into requests bounded by estimated tokens as well as count.

    ~4 chars per token is rough, but it only has to keep each request under a ceiling
    with margin to spare. A single text larger than the ceiling goes on its own — the
    chunker caps chunk size, so that should never happen.
    """
    out: list[list[str]] = []
    batch: list[str] = []
    tokens = 0
    for text in texts:
        estimate = len(text) // 4 + 1
        if batch and (tokens + estimate > max_tokens or len(batch) >= max_items):
            out.append(batch)
            batch, tokens = [], 0
        batch.append(text)
        tokens += estimate
    if batch:
        out.append(batch)
    return out


async def embed_query(text: str) -> list[float]:
    """Embed a question. RETRIEVAL_QUERY is a different projection from RETRIEVAL_DOCUMENT;
    mixing the two measurably degrades recall."""
    return (await embed_batch([text], "RETRIEVAL_QUERY"))[0]


async def generate(
    contents: list[types.Content],
    *,
    system_instruction: str,
    tools: list[types.Tool] | None = None,
    temperature: float = 0.0,
    response_schema: types.Schema | None = None,
) -> types.GenerateContentResponse:
    """Generate, moving to the next model rather than waiting on a throttled one.

    A 429 or 503 is about one model's capacity, and quotas are per model. Backing off on
    a single model spends the wait *and* forgoes the alternatives; switching costs nothing
    and is immediate. Only once every model has refused is it worth sleeping.
    """
    last: BaseException | None = None
    for round_ in range(2):
        for model in model_chain():
            try:
                return await _generate_once(
                    model, contents, system_instruction, tools, temperature, response_schema
                )
            except APIError as e:
                if not _transient(e):
                    raise  # a 400 will fail identically on every model
                _cool(model, e)
                last = e
        if round_ == 0:
            await asyncio.sleep(5 + random.random() * 5)
    raise AllModelsBusy([settings.gemini_model, *settings.gemini_fallback_models], last)


async def _generate_once(
    model: str,
    contents: list[types.Content],
    system_instruction: str,
    tools: list[types.Tool] | None,
    temperature: float,
    response_schema: types.Schema | None,
) -> types.GenerateContentResponse:
    """`tools` and `response_schema` are mutually exclusive — Gemini rejects both at once."""
    return await client().aio.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            tools=tools,
            temperature=temperature,
            response_mime_type="application/json" if response_schema else None,
            response_schema=response_schema,
            # We drive the tool loop ourselves: the search has to run against a
            # tenant-scoped connection, and we need to log and cap what it retrieves.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        ),
    )


async def stream(
    contents: list[types.Content],
    *,
    system_instruction: str,
    temperature: float = 0.0,
):
    """Yield response chunks, retrying only before the first one.

    `generate_content_stream` is lazy: the HTTP request fires on the first iteration, not
    on the call, so a decorator around the call retries nothing. Switching is only safe
    until a token has been delivered — after that the answer is partly on the user's
    screen and restarting would duplicate it.
    """
    last: BaseException | None = None
    for model in model_chain():
        started = False
        try:
            iterator = await client().aio.models.generate_content_stream(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=temperature,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            async for chunk in iterator:
                started = True
                yield chunk
            return
        except APIError as e:
            # Once a token is out the answer is partly on screen; restarting would
            # duplicate it, so a mid-stream failure is final whatever the cause.
            if started or not _transient(e):
                raise
            _cool(model, e)
            last = e
    raise AllModelsBusy([settings.gemini_model, *settings.gemini_fallback_models], last)


async def list_models() -> list[str]:
    """Used by /health so a stale model ID surfaces on deploy, not on a user's first question."""
    return [m.name async for m in await client().aio.models.list()]
