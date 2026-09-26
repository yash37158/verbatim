# Verbatim — Frontend Delivery Plan

Everything below is frontend-facing work. Each feature states what "done" means, so it can
be checked rather than assumed. Build order is deliberate: the landing page first because it
has no dependencies, then auth because every later feature needs to know who is asking.

**Current state.** The app works but has no concept of a person. `web/app/api/backend/`
attaches one `API_TOKEN` from an env var to every request, so every visitor is the same user
sharing one set of Spaces. That is the gap this plan closes.

---

## F1 · Landing page — `/`

**Why.** There is no explanation of what Verbatim is. `/` currently redirects straight into
the app, which only makes sense if you already know what it does.

**What it says.** The product's one real differentiator is not "AI reads your documents" —
everything claims that. It is that a quote is **checked against the source before you see
it**, and that the system says "not in your documents" instead of guessing. The page leads
with that.

**Scope**
- Hero: the promise, in one line, plus the two CTAs
- An interactive demo of the actual mechanic — an answer with citation chips that open the
  source passage. Showing beats describing, and the component already exists
- How it works: upload → indexed → ask → verify, four steps
- The honest section: what happens when the answer is not in your documents
- Footer

**Done — verified 2026-09-22**
- [x] `/` renders the landing page; `/spaces` and `/spaces/[id]` unchanged, both 200
- [x] The demo works with no backend and no API key (static data, client component)
- [x] 375px: `scrollWidth === innerWidth`, no horizontal scroll
- [x] Light and dark both checked in the browser
- [x] Chips are `<button>` with `aria-label` and `aria-pressed`
- [x] Zero failed resources on load; the page is statically prerendered
- [x] 97 backend + 8 frontend tests green, production build clean

**Note.** The CTAs point at `/spaces` rather than `/signin`, because `/signin` does not
exist until F3. Pointing at it now would ship a 404. Switch both when F3 lands.

**Structural change.** App chrome moved from the root layout into an `app/(app)/` route
group. The header showed a signed-in avatar, which is wrong on a public page, and the root
layout now holds only `<html>`/`<body>`. URLs are unaffected — route groups do not appear
in the path.

---

## F2 · Auth backend — Google sign-in

**Why.** The single thing preventing this from being usable by more than one person.

**Decision: Google SSO only for v1, no passwords.** Storing passwords means hashing,
rotation, reset emails, verification emails, breach response and a lockout policy — a large
surface for a feature Google already provides. Email/password can be added later if a user
without a Google account ever asks. This is a deliberate narrowing, not an oversight.

**Scope**
- `GET /auth/google` → redirect to Google with PKCE + `state`
- `GET /auth/google/callback` → verify `state`, exchange code, verify the ID token
  signature and `aud`, upsert the user
- Session: signed httpOnly cookie, `SameSite=Lax`, `Secure` in production
- `sessions` table: `id, user_id, expires_at, created_at, user_agent` — revocable, which a
  stateless JWT is not
- `GET /api/me`, `POST /auth/signout`
- `users` gains `google_sub` (unique), `name`, `avatar_url`
- Existing bearer-token auth stays for scripts and evals; the cookie is checked first

**Done — verified 2026-09-22.** Setup: [GOOGLE_SETUP.md](./GOOGLE_SETUP.md)
- [x] Two Google accounts produce two users with separate Spaces — one cannot list or reach
      the other's by id
- [x] Signing out deletes the row, so a copied token stops working too
- [x] Tampered, unknown and expired session cookies all rejected
- [x] `state` mismatch rejected, and a callback with no prior start rejected
- [x] An existing bearer account is **adopted** on first sign-in, not duplicated — anyone
      created by `bootstrap` keeps their Spaces
- [x] Bearer tokens still work, so the eval harness is unaffected
- [x] `next` cannot be an absolute URL — open-redirect guard, 7 hostile inputs tested
- [x] Session tokens stored as SHA-256; the raw token is never written down
- [x] 23 auth tests; 122 backend total

**Bug found and fixed in the proxy.** `fetch` follows redirects by default, so the Next
proxy would have chased Google's 302 itself and returned Google's HTML from our own origin.
It now uses `redirect: "manual"`, and passes `Set-Cookie` through `getSetCookie()` because a
`Headers` object collapses repeated headers into one comma-joined value, which corrupts
cookies.

**Not done on purpose.** The proxy still attaches `API_TOKEN` when present. Removing it is
F4's job; `current_user` checks the cookie first, so a signed-in user already wins.

---

## F3 · Sign in page — `/signin`

**Scope**
- Google button, product line, link back to the landing page
- `?next=` so a deep link survives the round trip
- Error states: cancelled, blocked cookies, Google unreachable

**Done — verified 2026-09-23**
- [x] Signed-in visitors are redirected away (`/signin` → `/spaces`, 307) — checked with a
      real session row, not a stub
- [x] `?next=/spaces/abc` survives the round trip into the Google URL and back
- [x] `auth_error` renders as a banner with `role="alert"`, not a stack trace
- [x] Open-redirect guard on the page too: `//evil.com` and `https://evil.com` both land on
      `/spaces`. The backend guards `next` as well; neither relies on the other
- [x] An unconfigured server redirects with a readable message instead of returning a raw
      503 body into the address bar
- [x] 375px, light and dark; Google's mark is inline SVG, so the button never waits on a
      network fetch to render
- [x] 27 auth tests, 124 backend total

**The landing page stays static.** Its CTAs point at `/signin` rather than checking for a
session, because that check would make the page dynamic. A signed-in visitor is bounced
through in one hop by `/signin` itself — one redirect, in exchange for a prerendered
landing page.

**`lib/session.ts` is separate from `lib/api.ts` on purpose.** `next/headers` only exists on
the server, and `lib/api.ts` is imported by client components. Keeping them apart makes the
boundary a file rather than a convention someone has to remember.

---

## F4 · Session-aware app

**Scope**
- Middleware guarding `/spaces/*`; unauthenticated → `/signin?next=…`
- The proxy forwards the session cookie instead of a static token; **delete `API_TOKEN`**
- Header shows the real name and avatar, with a sign-out menu
- 401 from the backend clears the client state and redirects

**Done — verified 2026-09-23**
- [x] `API_TOKEN` gone from `web/` source, `.env.example` and `.env.local`
- [x] Signed out, `/spaces` → `/signin?next=%2Fspaces`; a deep link keeps its id
- [x] Header shows the real account with a working sign-out; the hardcoded "Personal / YS"
      is gone
- [x] Sign-out deletes the row — the same token then gets 401 and `/spaces` bounces
- [x] Client bundle scanned: no `API_TOKEN`, no cookie name, no client secret, no
      `localhost:8000`, no email
- [x] Two accounts see different Spaces (tested in F2 at the API; the cookie is now the
      only credential the browser carries)
- [x] 124 backend + 8 frontend tests

**Deviation from the plan: the guard is split in two.** Middleware only checks that a
session cookie *exists* — validating one means asking the API, and doing that on every
request including static assets puts a round trip in front of the whole app. The real check
is in the `(app)` layout, which validates server-side before rendering. Middleware knows the
path and can preserve it in `next`; the layout knows the truth. A revoked cookie therefore
redirects without `next`, which is the one rough edge.

**Bug found: every 204 through the proxy was broken.** 204/205/304 are "null body statuses"
and handing the `Response` constructor a body for one aborts the request. Sign-out hung on
"Signing out…"; document and Space deletion would have failed the same way from the browser.

**Local sign-in without Google.** `uv run python -m app.bootstrap you@example.com --session`
mints a session and prints a one-line cookie to paste into the console. It needs shell
access to the database, so it adds no attack surface to the running service.

---

## F5 · Chat history

**Why.** Conversations are created and stored, and the model auto-titles them, but the UI
can only ever show the current one. Every past answer is unreachable.

**Scope**
- Conversation list in the Space sidebar, newest first, with titles
- **New chat**; switching loads that conversation's messages and citations
- Rename inline; delete with confirmation
- Deep link: `/spaces/<id>?c=<conversationId>`
- Empty state for a Space with no conversations

**Backend gaps to close**
- `PATCH /api/conversations/{id}` (rename) — does not exist
- `DELETE /api/conversations/{id}` — does not exist
- `GET /api/conversations/{id}/messages` returns citations but not `searches`; include them

**Done — 2026-09-26**
- [x] Reload restores the open conversation exactly — `?c=<id>` is read server-side and the
      messages are preloaded, so there is no flash of empty state
- [x] Citation chips in a restored conversation open the source drawer (citations are stored
      whole, context included)
- [x] Renaming persists; deleting cascades to messages; both refused across tenants (404)
- [x] The three missing endpoints now exist: `PATCH` and `DELETE /api/conversations/{id}`,
      and messages return `searches` and `created_at`
- [x] The list is ordered by last activity and carries a message count
- [ ] Cross-tab deletion visibility — deferred; the list refreshes on navigation, not on a poll

**Known rough edge, found while verifying.** The model sometimes answers with markdown
bullets, and `AnswerText`'s inline formatter only handles bold and code — so a leading `*`
renders literally. Not F5, and not trivial to fix well: citations are placed by sentence
index, and bullets and sentence splitting interact. Worth its own small change rather than
a patch dropped in here.

**Design.** `ChatPane` is keyed on the active conversation id, so switching threads
remounts it with the right messages. That is simpler and safer than resetting a dozen
pieces of state inside the component, and it means a fresh chat and a restored one are
the same code path with different initial props.

---

## F6 · Space management

**Scope**
- Rename and delete a Space from its workspace header (both APIs exist, neither has a button)
- Delete asks for confirmation and states what is removed
- Document delete and retry already exist; surface delete next to retry

**Done when**
- [ ] Deleting a Space returns to `/spaces` and it is gone from the list
- [ ] Renaming updates the header and the Spaces list

---

## Order and reasoning

1. **F1 Landing** — no dependencies, and nothing else is safe to show without it
2. **F2 Auth backend** — everything below needs a user
3. **F3 Sign in** — thin once F2 exists
4. **F4 Session-aware app** — removes the shared-token flaw
5. **F5 Chat history** — the largest user-visible gain, needs a user to be meaningful
6. **F6 Space management** — small, mostly wiring existing endpoints

## Rules for every feature

- Existing tests stay green; new behaviour gets its own test
- `npm run build` and `uv run pytest` both pass before a feature is called done
- Verified in a browser, not only by the type checker
- No secret reaches the client bundle
