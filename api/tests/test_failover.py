"""Model failover and embedding pacing — the two levers against free-tier limits."""

import asyncio

import pytest
from google.genai.errors import ClientError, ServerError

from app import gemini
from app.config import settings


def throttled(code=429):
    payload = {"error": {"code": code, "status": "RESOURCE_EXHAUSTED",
                         "message": "quota. 'retryDelay': '30s'"}}
    return ClientError(429, payload) if code == 429 else ServerError(503, {
        "error": {"code": 503, "status": "UNAVAILABLE", "message": "overloaded"}})


@pytest.fixture(autouse=True)
def clean_chain(monkeypatch):
    gemini._cooling.clear()
    monkeypatch.setattr(settings, "gemini_model", "primary")
    monkeypatch.setattr(settings, "gemini_fallback_models", ["second", "third"])
    yield
    gemini._cooling.clear()


# ------------------------------------------------------------------ failover


async def test_a_throttled_model_is_skipped_rather_than_waited_on(monkeypatch):
    """Quotas are per model, so the next one is capacity available right now. Sleeping on
    a throttled model spends the wait and forgoes the alternative."""
    tried = []

    async def once(model, *args):
        tried.append(model)
        if model in ("primary", "second"):
            raise throttled()
        return "answer"

    monkeypatch.setattr(gemini, "_generate_once", once)
    result = await gemini.generate([], system_instruction="x")

    assert result == "answer"
    assert tried == ["primary", "second", "third"], "it should walk the chain, not retry one"


async def test_a_model_that_refused_is_not_tried_again_immediately(monkeypatch):
    """Opening every question by burning a request on a pool known to be empty is waste."""
    async def once(model, *args):
        if model == "primary":
            raise throttled()
        return "answer"

    monkeypatch.setattr(gemini, "_generate_once", once)
    await gemini.generate([], system_instruction="x")

    assert gemini.model_chain()[0] == "second", "primary is cooling and moves to the back"
    assert "primary" not in gemini.model_chain()


async def test_when_every_model_is_cooling_it_tries_anyway(monkeypatch):
    """A stale local estimate is not a reason to refuse a request the provider may serve."""
    for m in ("primary", "second", "third"):
        gemini._cooling[m] = float("inf")
    assert gemini.model_chain() == ["primary", "second", "third"]


async def test_a_bad_request_fails_on_the_first_model_without_burning_the_chain(monkeypatch):
    """A 400 will fail identically everywhere; walking the chain just wastes quota."""
    tried = []

    async def once(model, *args):
        tried.append(model)
        raise ClientError(400, {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                                          "message": "bad schema"}})

    monkeypatch.setattr(gemini, "_generate_once", once)
    with pytest.raises(ClientError):
        await gemini.generate([], system_instruction="x")
    assert tried == ["primary"]


async def test_overload_fails_over_the_same_way_as_quota(monkeypatch):
    tried = []

    async def once(model, *args):
        tried.append(model)
        if model == "primary":
            raise throttled(503)
        return "ok"

    monkeypatch.setattr(gemini, "_generate_once", once)
    assert await gemini.generate([], system_instruction="x") == "ok"
    assert tried == ["primary", "second"]


# ------------------------------------------------------------------ pacing


async def test_pacing_widens_on_a_refusal_and_narrows_on_success(monkeypatch):
    monkeypatch.setattr(settings, "embed_min_gap", 0.01)
    monkeypatch.setattr(settings, "embed_max_gap", 10.0)
    pace = gemini.Pace()

    start = pace.gap
    pace.throttled()
    assert pace.gap > start, "a 429 must slow the next request down"

    widened = pace.gap
    for _ in range(5):
        pace.eased()
    assert pace.gap < widened, "sustained success should creep back towards full speed"
    assert pace.gap >= settings.embed_min_gap


async def test_the_gap_is_bounded_at_both_ends(monkeypatch):
    monkeypatch.setattr(settings, "embed_min_gap", 0.5)
    monkeypatch.setattr(settings, "embed_max_gap", 4.0)
    pace = gemini.Pace()

    for _ in range(20):
        pace.throttled()
    assert pace.gap == 4.0, "backoff must not grow without limit"
    for _ in range(100):
        pace.eased()
    assert pace.gap == 0.5, "and must return to full speed, not crawl forever"


async def test_requests_are_actually_spaced_apart():
    """The point is wall-clock separation, not just a number in a field."""
    pace = gemini.Pace()
    pace._gap = 0.05
    loop = asyncio.get_running_loop()
    before = loop.time()
    for _ in range(4):
        await pace.wait()
    assert loop.time() - before >= 0.15, "four requests at a 50ms gap cannot finish instantly"


async def test_an_exhausted_chain_says_so_rather_than_blaming_the_last_model(monkeypatch):
    """Reporting only the final model's error reads as a blip, when in fact there is no
    capacity anywhere — a different situation calling for a different response."""
    async def always_throttled(model, *args):
        raise throttled()

    monkeypatch.setattr(gemini, "_generate_once", always_throttled)
    real_sleep = asyncio.sleep  # bind before patching, or the stub calls itself
    monkeypatch.setattr(asyncio, "sleep", lambda *_, **__: real_sleep(0))

    with pytest.raises(gemini.AllModelsBusy) as caught:
        await gemini.generate([], system_instruction="x")

    message = gemini.friendly_error(caught.value)
    assert "All 3 models" in message
    assert "frees up in about" in message, "the user needs a time, not just a diagnosis"
    assert "overloaded right now" in message
    assert "RESOURCE_EXHAUSTED" not in message
