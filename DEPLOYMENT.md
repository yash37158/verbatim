# Deploying Verbatim for free

Free tiers move constantly, so everything below was checked against the providers' own
pricing pages on **2026-09-27**. Re-check before relying on it.

---

## The stack

| Piece | Provider | Free tier | Card needed |
|---|---|---|---|
| Postgres + pgvector | **Neon** | Permanent free. 0.5 GB storage, 100 CU-hours/month per project, **5 GB object storage included** | No |
| Backend (FastAPI) | **Hugging Face Spaces** | CPU Basic: 2 vCPU, **16 GB RAM**, Docker | No |
| Frontend (Next.js) | **Vercel** Hobby | Free, no sleep. Non-commercial use only | No |
| Embeddings + generation | **Gemini** free tier | 20 generate requests/day *per model*; the app already fails over across a chain of them | No |

Neon's included object storage is what makes this work without a fourth provider —
uploaded files have somewhere to live that survives a redeploy.

**Capacity.** 0.5 GB of Postgres holds roughly 50,000 chunks, about 25,000 pages. Ample
for a demo, not for a real corpus.

### Alternatives considered

- **Render** free web service — simpler to set up, but **sleeps after 15 minutes** of no
  traffic with a ~1 minute cold start, and has an ephemeral filesystem. Fine if you accept
  that the first visitor after a quiet spell waits a minute.
- **Koyeb** — no longer has a usable free tier for services; Pro starts at $29/month.
- **Fly.io, Railway** — no standing free allowance for this shape of app.
- **Oracle Cloud Always Free** — genuinely free and genuinely always-on, but needs a card
  and considerably more setup. The right answer if the cold start becomes intolerable.

---

## What has to change first

Deploying is not only a hosting choice. Seven things in this repo assume localhost:

1. **No Dockerfile** for `api/`. Needs writing.
2. **No migrations.** `schema.sql` is applied by hand. Needs to run on deploy, or be a
   documented manual step against the Neon branch.
3. **Storage is local disk** (`ingest.storage_path`). On any of these hosts the filesystem
   is ephemeral, so uploads vanish on redeploy. Chunks survive in Postgres so search keeps
   working, but re-ingestion and "Open in document" break. Move it to Neon object storage —
   two call sites, as noted in `api/README.md`.
4. **`COOKIE_SECURE=false`.** Must be `true` in production or browsers will discard the
   session cookie over HTTPS and nobody can sign in.
5. **`PUBLIC_BASE_URL`, `API_URL` and `CORS_ORIGINS`** all point at localhost.
6. **Google OAuth** needs the production callback registered:
   `https://<your-frontend>/api/backend/auth/google/callback`, matched character for
   character. See [GOOGLE_SETUP.md](GOOGLE_SETUP.md).
7. **The ingestion worker runs inside the API process.** On a host that sleeps, a queued
   document waits until the next visitor wakes the service. Acceptable, but say so rather
   than let it look broken.

Roughly half a day, most of it in items 1–3.

---

## Read this before making it public

**There is no rate limiting, and sign-in accepts any Google account.**

Anyone who finds the URL can sign in and spend your Gemini quota — which has already run
out several times from local testing by one person. A public URL turns that into a
free-for-all.

Two cheap mitigations, either of which is enough for a demo:

- **An email allowlist** — roughly ten lines in `auth.upsert_user`, refusing any address
  not on a configured list. Turns "anyone on the internet" into "you and whoever you
  invite". This is the one I would do.
- **A per-user daily question cap** — a count over `messages` in the last 24 hours,
  checked before the agent runs.

Neither is a substitute for real rate limiting (still listed as a production gap in the
[README](README.md#project-status)), but both stop the obvious abuse.

---

## Suggested order

1. Email allowlist — before anything is reachable
2. Dockerfile for `api/`
3. Object storage, replacing local disk
4. Deploy config: environment variables, migration step, OAuth callback
5. Create the Neon project, the Space and the Vercel project, then connect them

Steps 1–4 are code. Step 5 needs your accounts.
