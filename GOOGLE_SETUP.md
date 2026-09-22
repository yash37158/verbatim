# Enabling Google sign-in

Five minutes in the Google Cloud Console, then two values in `api/.env`.

## 1 · Create the OAuth client

1. [console.cloud.google.com](https://console.cloud.google.com) → create or pick a project
2. **APIs & Services → OAuth consent screen**
   - User type: **External**
   - App name, your email for support and developer contact — that is all that is required
   - Scopes: the defaults (`openid`, `email`, `profile`) are enough; add nothing
   - **Test users**: add every Google account you intend to sign in with. While the app is
     unpublished only these accounts can sign in, and the error if you forget is an
     unhelpful "access blocked"
3. **APIs & Services → Credentials → Create credentials → OAuth client ID**
   - Type: **Web application**
   - **Authorised redirect URI**, exactly, including the port:

     ```
     http://localhost:3000/api/backend/auth/google/callback
     ```

   Google matches this string character for character. A trailing slash, `127.0.0.1` instead
   of `localhost`, or the wrong port all produce `redirect_uri_mismatch`.

## 2 · Put the credentials in `api/.env`

```bash
GOOGLE_CLIENT_ID=<the client id>.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=<the client secret>
PUBLIC_BASE_URL=http://localhost:3000
```

Restart the API. `GET /auth/google` returns 503 until the client id is set, rather than
failing obscurely at Google.

## 3 · Try it

Visit **http://localhost:3000/api/backend/auth/google** — you should reach Google's account
chooser and come back signed in. Until F3 lands there is no sign-in button; that URL is the
entry point.

Check it worked:

```bash
curl -s -c /tmp/cookies http://localhost:3000/api/backend/api/me
```

## Going to production

- Set `COOKIE_SECURE=true`. Browsers discard a `Secure` cookie over plain http, which is why
  it is off locally
- `PUBLIC_BASE_URL` becomes your real origin, and that origin's callback URL must be added
  to the same OAuth client
- Publish the consent screen, or only your listed test users can sign in
- `SESSION_DAYS` (default 30) controls how long a session lasts

## How it works

`/auth/google` mints a random `state` and a PKCE verifier, stores both in httpOnly cookies
and redirects to Google. The callback checks `state` against the cookie — without that an
attacker can hand you their own authorization code and sign your browser into *their*
account. The ID token's signature, issuer and audience are verified even though it came
straight from Google over TLS, because that guarantee should not depend on the delivery path.

Sessions are rows in a table, not signed tokens, so signing out genuinely invalidates one.
The token is stored as a SHA-256 hash, like a password: a database leak yields hashes rather
than working sessions.
