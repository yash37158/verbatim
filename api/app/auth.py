"""Sessions and Google sign-in.

Two things here are trust boundaries and are written accordingly rather than briefly:

  * the OAuth callback, where `state` is the only thing standing between us and an attacker
    completing a sign-in on someone else's behalf;
  * the session cookie, which is a bearer credential for an entire account.

Session tokens are stored hashed, exactly like passwords. A database leak then exposes
hashes rather than usable sessions. Sessions live in a table rather than a signed JWT so
that signing out actually invalidates them — a stateless token cannot be revoked.
"""

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import asyncpg
import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token  # re-exported for tests to stub

from .config import settings

SESSION_COOKIE = "vb_session"
STATE_COOKIE = "vb_oauth_state"
VERIFIER_COOKIE = "vb_oauth_verifier"
NEXT_COOKIE = "vb_oauth_next"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
STATE_TTL_SECONDS = 600  # a sign-in that takes longer than ten minutes can start again


class AuthError(Exception):
    """Something about the callback did not add up. The message is user-facing."""


# --------------------------------------------------------------------------- sessions


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create_session(conn: asyncpg.Connection, user_id, user_agent: str | None) -> str:
    """Returns the raw token. Only its hash is stored, so this is the last time we see it."""
    token = secrets.token_urlsafe(32)
    await conn.execute(
        """insert into sessions (user_id, token_hash, expires_at, user_agent)
           values ($1, $2, $3, $4)""",
        user_id,
        _hash(token),
        datetime.now(UTC) + timedelta(days=settings.session_days),
        (user_agent or "")[:300],
    )
    return token


async def user_for_session(conn: asyncpg.Connection, token: str) -> asyncpg.Record | None:
    return await conn.fetchrow(
        """select u.* from sessions s join users u on u.id = s.user_id
            where s.token_hash = $1 and s.expires_at > now()""",
        _hash(token),
    )


async def revoke_session(conn: asyncpg.Connection, token: str) -> None:
    await conn.execute("delete from sessions where token_hash = $1", _hash(token))


async def purge_expired(conn: asyncpg.Connection) -> int:
    return int((await conn.execute("delete from sessions where expires_at < now()")).split()[-1])


# --------------------------------------------------------------------------- oauth


def safe_next(value: str | None) -> str:
    """Only same-site paths.

    An unchecked `next` turns sign-in into an open redirect: a link that authenticates the
    user and then lands them on an attacker's page wearing our name. `//evil.com` and
    `https://evil.com` are both rejected; anything not starting with a single `/` is.
    """
    if not value or not value.startswith("/") or value.startswith("//"):
        return "/spaces"
    return value


def redirect_uri() -> str:
    """Registered with Google verbatim. It points at the frontend, whose proxy forwards the
    callback here — so the session cookie is set on the origin the browser is actually on."""
    return f"{settings.public_base_url}/api/backend/auth/google/callback"


def authorization_url(state: str, verifier: str) -> str:
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    return GOOGLE_AUTH_URL + "?" + urlencode(
        {
            "client_id": settings.google_client_id,
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "online",
            "prompt": "select_account",
        }
    )


def check_state(received: str | None, expected: str | None) -> None:
    """The CSRF gate. Without it, an attacker can feed us their own authorization code and
    sign the victim's browser into the attacker's account."""
    if not received or not expected or not secrets.compare_digest(received, expected):
        raise AuthError("This sign-in link has expired or was tampered with. Try again.")


async def exchange_code(code: str, verifier: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as http:
        response = await http.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri(),
                "grant_type": "authorization_code",
                "code_verifier": verifier,
            },
        )
    if response.status_code != 200:
        raise AuthError("Google rejected the sign-in. Try again.")
    return response.json()


def verify_id_token(raw: str) -> dict:
    """Verify signature, issuer, audience and expiry.

    Google's docs allow skipping this when the token came straight from the token endpoint
    over TLS. It is done anyway: it costs one cached JWKS fetch, and it means the guarantee
    holds even if this token later arrives by some other path.
    """
    try:
        claims = google_id_token.verify_oauth2_token(
            raw, google_requests.Request(), settings.google_client_id
        )
    except ValueError as e:
        raise AuthError("Could not verify the Google sign-in.") from e

    if claims.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise AuthError("Unexpected token issuer.")
    if not claims.get("email"):
        raise AuthError("Google did not return an email address.")
    if not claims.get("email_verified"):
        raise AuthError("Your Google email address is not verified.")
    return claims


async def upsert_user(conn: asyncpg.Connection, claims: dict) -> asyncpg.Record:
    """Match on `sub` first — Google's stable id. Email can change hands; `sub` cannot.

    An existing bearer-token account with the same email is adopted rather than duplicated,
    so anyone created by `bootstrap` keeps their Spaces when they first sign in.
    """
    return await conn.fetchrow(
        """insert into users (email, google_sub, name, avatar_url)
           values ($1, $2, $3, $4)
           on conflict (email) do update
               set google_sub = excluded.google_sub,
                   name = coalesce(excluded.name, users.name),
                   avatar_url = coalesce(excluded.avatar_url, users.avatar_url)
           returning *""",
        claims["email"].strip().lower(),
        claims["sub"],
        claims.get("name"),
        claims.get("picture"),
    )
