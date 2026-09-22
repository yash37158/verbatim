"""Create a user, and optionally a session so you can sign in without Google.

    uv run python -m app.bootstrap you@example.com            # prints an API key
    uv run python -m app.bootstrap you@example.com --session  # also mints a browser session

The session flag exists because the app requires a signed-in user, and setting up Google
OAuth is a five-minute detour you may not want to take right now. It is a local development
convenience, not an endpoint: minting a session needs shell access to the database, so it
adds no attack surface to the running service.
"""

import argparse
import asyncio
import secrets

from .auth import SESSION_COOKIE, create_session
from .db import acquire, disconnect


async def main(email: str, want_session: bool) -> None:
    async with acquire() as conn:
        key = "vb_" + secrets.token_urlsafe(32)
        user = await conn.fetchrow(
            """insert into users (email, api_key) values ($1, $2)
               on conflict (email) do update set api_key = coalesce(users.api_key, excluded.api_key)
               returning id, api_key""",
            email.strip().lower(), key,
        )
        print(f"api key: {user['api_key']}")

        if want_session:
            token = await create_session(conn, user["id"], "bootstrap")
            print("\nTo sign in, paste this into the browser console at http://localhost:3000")
            print("then reload:\n")
            print(f'  document.cookie = "{SESSION_COOKIE}={token}; path=/; max-age=2592000"')
    await disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m app.bootstrap")
    parser.add_argument("email")
    parser.add_argument("--session", action="store_true",
                        help="also mint a browser session, for local use without Google")
    args = parser.parse_args()
    asyncio.run(main(args.email, args.session))
