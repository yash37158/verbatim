"""Sessions and the Google callback.

Google itself is stubbed — the round trip is theirs to test. Everything this codebase is
responsible for is exercised for real: the CSRF gate, the open-redirect guard, token
hashing, revocation, expiry, and the fact that a cookie identifies exactly one account.
"""

import hashlib
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio

from app import auth

from app.config import settings
from app.db import acquire, disconnect
from app.main import app

CLAIMS = {
    "sub": "google-sub-12345",
    "email": "Ada@Example.com",
    "email_verified": True,
    "name": "Ada Lovelace",
    "picture": "https://example.com/ada.png",
}


@pytest_asyncio.fixture
async def client(monkeypatch):
    monkeypatch.setattr(settings, "google_client_id", "test-client-id")
    monkeypatch.setattr(settings, "google_client_secret", "test-secret")
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:3000")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                 follow_redirects=False) as c:
        yield c
    await disconnect()


@pytest_asyncio.fixture
async def cleanup():
    emails: list[str] = []
    yield emails
    async with acquire() as conn:
        for email in emails:
            await conn.execute("delete from users where email = $1", email.lower())


def stub_google(monkeypatch, claims=None):
    async def fake_exchange(code, verifier):
        assert code and verifier, "the code and PKCE verifier must both reach Google"
        return {"id_token": "stub"}

    monkeypatch.setattr(auth, "exchange_code", fake_exchange)
    monkeypatch.setattr(auth, "verify_id_token", lambda raw: claims or CLAIMS)


# ---------------------------------------------------------------- open redirect


@pytest.mark.parametrize("value", [
    "//evil.example.com", "https://evil.example.com", "http://evil.example.com",
    "javascript:alert(1)", "", None, "evil.com",
])
def test_next_only_accepts_same_site_paths(value):
    """Unchecked, `next` turns sign-in into an open redirect: a link that authenticates the
    user and then lands them somewhere hostile, wearing our name."""
    assert auth.safe_next(value) == "/spaces"


@pytest.mark.parametrize("value", ["/spaces", "/spaces/abc-123", "/spaces?c=1"])
def test_next_keeps_a_legitimate_destination(value):
    assert auth.safe_next(value) == value


# ---------------------------------------------------------------- the flow


async def test_start_sets_the_cookies_the_callback_will_check(client):
    r = await client.get("/auth/google?next=/spaces/abc")
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert "code_challenge_method=S256" in r.headers["location"]

    for name in (auth.STATE_COOKIE, auth.VERIFIER_COOKIE, auth.NEXT_COOKIE):
        assert name in r.cookies, f"{name} was not set"
    raw = r.headers.get_list("set-cookie")
    assert all("HttpOnly" in c for c in raw), "these must be unreachable from JavaScript"
    assert all("samesite=lax" in c.lower() for c in raw)  # the attribute is case-insensitive


async def test_a_full_sign_in_creates_a_user_and_a_session(client, cleanup, monkeypatch):
    stub_google(monkeypatch)
    cleanup.append(CLAIMS["email"])

    start = await client.get("/auth/google?next=/spaces/xyz")
    state = start.cookies[auth.STATE_COOKIE]

    done = await client.get(f"/auth/google/callback?code=abc&state={state}")
    assert done.status_code == 302
    assert done.headers["location"] == "http://localhost:3000/spaces/xyz", "next was honoured"

    session = done.cookies[auth.SESSION_COOKIE]
    assert session
    cookie_header = next(c for c in done.headers.get_list("set-cookie")
                         if c.startswith(auth.SESSION_COOKIE))
    assert "HttpOnly" in cookie_header, "a session cookie readable from JS is a stolen session"
    assert "samesite=lax" in cookie_header.lower()

    me = await client.get("/api/me", cookies={auth.SESSION_COOKIE: session})
    assert me.status_code == 200
    assert me.json()["email"] == "ada@example.com", "email is normalised to lower case"
    assert me.json()["name"] == "Ada Lovelace"


async def test_the_session_token_is_stored_hashed_not_in_the_clear(client, cleanup, monkeypatch):
    """A database leak should yield hashes, not working sessions."""
    stub_google(monkeypatch)
    cleanup.append(CLAIMS["email"])

    start = await client.get("/auth/google")
    done = await client.get(f"/auth/google/callback?code=abc&state={start.cookies[auth.STATE_COOKIE]}")
    token = done.cookies[auth.SESSION_COOKIE]

    async with acquire() as conn:
        stored = await conn.fetchval("select token_hash from sessions order by created_at desc limit 1")
    assert stored != token
    assert stored == hashlib.sha256(token.encode()).hexdigest()


async def test_a_mismatched_state_is_refused(client, monkeypatch):
    """The CSRF gate. Without it an attacker feeds us their own authorization code and the
    victim's browser ends up signed into the attacker's account."""
    stub_google(monkeypatch)
    await client.get("/auth/google")

    r = await client.get("/auth/google/callback?code=abc&state=not-the-one-we-issued")
    assert r.status_code == 302
    assert "auth_error" in r.headers["location"]
    assert auth.SESSION_COOKIE not in r.cookies, "no session may be created"


async def test_a_callback_with_no_prior_start_is_refused(client, monkeypatch):
    stub_google(monkeypatch)
    r = await client.get("/auth/google/callback?code=abc&state=anything")
    assert "auth_error" in r.headers["location"]
    assert auth.SESSION_COOKIE not in r.cookies


async def test_a_cancelled_sign_in_reads_as_cancelled(client):
    r = await client.get("/auth/google/callback?error=access_denied")
    assert r.status_code == 302
    assert "cancelled" in r.headers["location"].lower()


@pytest.mark.parametrize("claims, expected", [
    ({"iss": "https://accounts.google.com", "email": "x@y.com", "email_verified": False,
      "sub": "1"}, "not verified"),
    ({"iss": "https://accounts.google.com", "email": "", "email_verified": True,
      "sub": "1"}, "did not return an email"),
    ({"iss": "https://evil.example.com", "email": "x@y.com", "email_verified": True,
      "sub": "1"}, "issuer"),
])
def test_id_token_claims_are_checked(monkeypatch, claims, expected):
    """An unverified address lets anyone claim one they do not control; a wrong issuer means
    the token was not minted by Google at all."""
    monkeypatch.setattr(auth.google_id_token, "verify_oauth2_token",
                        lambda *a, **k: claims)
    with pytest.raises(auth.AuthError, match=expected):
        auth.verify_id_token("stub")


async def test_a_quoted_next_cookie_still_resolves(client, cleanup, monkeypatch):
    """Starlette quotes a cookie value containing a slash. If it did not unquote on read,
    `next` would be silently discarded and every sign-in would land on /spaces."""
    stub_google(monkeypatch)
    cleanup.append(CLAIMS["email"])
    start = await client.get("/auth/google?next=/spaces/deep/link")
    done = await client.get(
        f"/auth/google/callback?code=abc&state={start.cookies[auth.STATE_COOKIE]}")
    assert done.headers["location"] == "http://localhost:3000/spaces/deep/link"


# ---------------------------------------------------------------- session lifecycle


async def test_signing_out_invalidates_the_session_server_side(client, cleanup, monkeypatch):
    """Clearing the cookie alone leaves a copied token working."""
    stub_google(monkeypatch)
    cleanup.append(CLAIMS["email"])
    start = await client.get("/auth/google")
    done = await client.get(f"/auth/google/callback?code=abc&state={start.cookies[auth.STATE_COOKIE]}")
    token = done.cookies[auth.SESSION_COOKIE]

    assert (await client.get("/api/me", cookies={auth.SESSION_COOKIE: token})).status_code == 200
    assert (await client.post("/auth/signout", cookies={auth.SESSION_COOKIE: token})).status_code == 204
    assert (await client.get("/api/me", cookies={auth.SESSION_COOKIE: token})).status_code == 401


async def test_an_unknown_or_expired_session_is_rejected(client, cleanup, monkeypatch):
    assert (await client.get("/api/me", cookies={auth.SESSION_COOKIE: "made-up"})).status_code == 401

    stub_google(monkeypatch)
    cleanup.append(CLAIMS["email"])
    start = await client.get("/auth/google")
    done = await client.get(f"/auth/google/callback?code=abc&state={start.cookies[auth.STATE_COOKIE]}")
    token = done.cookies[auth.SESSION_COOKIE]

    async with acquire() as conn:
        await conn.execute("update sessions set expires_at = $1 where token_hash = $2",
                           datetime.now(UTC) - timedelta(seconds=1),
                           hashlib.sha256(token.encode()).hexdigest())
    assert (await client.get("/api/me", cookies={auth.SESSION_COOKIE: token})).status_code == 401


async def test_no_credentials_at_all_is_a_401(client):
    assert (await client.get("/api/me")).status_code == 401
    assert (await client.get("/api/spaces")).status_code == 401


async def test_two_google_accounts_are_two_separate_tenants(client, cleanup, monkeypatch):
    """The whole point of F2. Before this, every visitor shared one set of Spaces."""
    people = [
        {"sub": "sub-ada", "email": "ada@example.com", "email_verified": True, "name": "Ada"},
        {"sub": "sub-alan", "email": "alan@example.com", "email_verified": True, "name": "Alan"},
    ]
    sessions = {}
    for person in people:
        cleanup.append(person["email"])
        stub_google(monkeypatch, person)
        start = await client.get("/auth/google")
        done = await client.get(
            f"/auth/google/callback?code=abc&state={start.cookies[auth.STATE_COOKIE]}")
        sessions[person["name"]] = done.cookies[auth.SESSION_COOKIE]

    assert sessions["Ada"] != sessions["Alan"]

    made = await client.post("/api/spaces", json={"name": "Ada's private notes"},
                             cookies={auth.SESSION_COOKIE: sessions["Ada"]})
    assert made.status_code == 201
    space_id = made.json()["id"]

    alan_list = await client.get("/api/spaces", cookies={auth.SESSION_COOKIE: sessions["Alan"]})
    assert alan_list.json() == [], "Alan must not see Ada's Space"

    peek = await client.get(f"/api/spaces/{space_id}",
                            cookies={auth.SESSION_COOKIE: sessions["Alan"]})
    assert peek.status_code == 404, "and must not be able to reach it by id"

    mine = await client.get("/api/spaces", cookies={auth.SESSION_COOKIE: sessions["Ada"]})
    assert [s["name"] for s in mine.json()] == ["Ada's private notes"]


async def test_an_existing_bearer_account_is_adopted_not_duplicated(client, cleanup, monkeypatch):
    """Anyone created by `bootstrap` keeps their Spaces the first time they sign in."""
    import secrets as _secrets

    email = f"legacy-{_secrets.token_hex(4)}@example.com"
    cleanup.append(email)
    key = "vb_" + _secrets.token_urlsafe(16)
    async with acquire() as conn:
        user_id = await conn.fetchval(
            "insert into users (email, api_key) values ($1, $2) returning id", email, key)
        await conn.execute("insert into spaces (user_id, name) values ($1, 'Old Space')", user_id)

    stub_google(monkeypatch, {"sub": "sub-legacy", "email": email.upper(),
                              "email_verified": True, "name": "Legacy"})
    start = await client.get("/auth/google")
    done = await client.get(
        f"/auth/google/callback?code=abc&state={start.cookies[auth.STATE_COOKIE]}")
    session = done.cookies[auth.SESSION_COOKIE]

    async with acquire() as conn:
        count = await conn.fetchval("select count(*) from users where email = $1", email)
    assert count == 1, "signing in must not create a second account for the same person"

    spaces = await client.get("/api/spaces", cookies={auth.SESSION_COOKIE: session})
    assert [s["name"] for s in spaces.json()] == ["Old Space"]

    still_works = await client.get("/api/spaces", headers={"Authorization": f"Bearer {key}"})
    assert still_works.status_code == 200, "the eval harness still uses bearer tokens"


async def test_an_unconfigured_server_redirects_instead_of_returning_raw_json(client, monkeypatch):
    """This is a page someone navigated to, not an API call they made. A 503 body renders
    as JSON in the address bar."""
    monkeypatch.setattr(settings, "google_client_id", "")
    r = await client.get("/auth/google")
    assert r.status_code == 302
    assert "/signin?auth_error=" in r.headers["location"]
    assert "not+configured" in r.headers["location"] or "not%20configured" in r.headers["location"]


async def test_failures_land_on_the_sign_in_page(client, monkeypatch):
    """Not the landing page — the user is mid-sign-in and needs the button again."""
    stub_google(monkeypatch)
    await client.get("/auth/google")
    r = await client.get("/auth/google/callback?code=abc&state=wrong")
    assert r.headers["location"].startswith("http://localhost:3000/signin?auth_error=")
