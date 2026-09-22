import asyncio
import hashlib
import json
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import quote
from uuid import UUID

import asyncpg
from fastapi import Cookie, Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, StreamingResponse
from google.genai import types

from . import auth, ingest
from .agent import answer
from .gemini import friendly_error
from .config import settings
from .db import acquire, connect, disconnect
from .models import AskIn, SpaceIn, SpacePatch

log = logging.getLogger("verbatim")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect()
    # Expired sessions are already refused by the lookup; this only stops the table growing
    # forever. Once per start is often enough for housekeeping.
    async with acquire() as conn:
        gone = await auth.purge_expired(conn)
        if gone:
            log.info("purged %d expired sessions", gone)
    stop = asyncio.Event()
    worker = asyncio.create_task(ingest.run_worker(stop)) if settings.run_worker else None
    try:
        yield
    finally:
        stop.set()
        if worker:
            await worker
        await disconnect()


app = FastAPI(title="Verbatim API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ auth & scoping


async def db() -> AsyncIterator[asyncpg.Connection]:
    async with acquire() as conn:
        yield conn


async def current_user(
    conn: asyncpg.Connection = Depends(db),
    authorization: str | None = Header(default=None),
    vb_session: str | None = Cookie(default=None),
) -> asyncpg.Record:
    """Session cookie first, then bearer token.

    The cookie is what a browser carries and it is revocable. The bearer token stays for
    scripts and the eval harness, which have no browser to hold a cookie.
    """
    if vb_session:
        user = await auth.user_for_session(conn, vb_session)
        if user is not None:
            return user
    if authorization and authorization.startswith("Bearer "):
        user = await conn.fetchrow("select * from users where api_key = $1", authorization[7:])
        if user is not None:
            return user
    raise HTTPException(401, "Not signed in")


# ------------------------------------------------------------------ google sign-in

SIGNIN_PATH = "/signin"


def _set_cookie(response: Response, name: str, value: str, max_age: int) -> None:
    response.set_cookie(
        name, value,
        max_age=max_age,
        httponly=True,       # unreachable from JavaScript, so XSS cannot lift it
        samesite="lax",      # survives the redirect back from Google; blocks cross-site POSTs
        secure=settings.cookie_secure,
        path="/",
    )


@app.get("/auth/google")
async def google_start(next: str = "/"):
    """Begin sign-in. `state` and the PKCE verifier are minted here and held in httpOnly
    cookies, so the callback can prove the flow is the one this browser started."""
    if not settings.google_client_id:
        # A redirect with a readable message, not raw JSON in the browser — this is a page
        # the user navigated to, not an API call they made.
        return RedirectResponse(
            f"{settings.public_base_url}{SIGNIN_PATH}?auth_error="
            + quote("Google sign-in is not configured on this server yet."),
            status_code=302,
        )

    state = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    response = RedirectResponse(auth.authorization_url(state, verifier), status_code=302)
    _set_cookie(response, auth.STATE_COOKIE, state, auth.STATE_TTL_SECONDS)
    _set_cookie(response, auth.VERIFIER_COOKIE, verifier, auth.STATE_TTL_SECONDS)
    _set_cookie(response, auth.NEXT_COOKIE, auth.safe_next(next), auth.STATE_TTL_SECONDS)
    return response


@app.get("/auth/google/callback")
async def google_callback(
    request: Request,
    code: str | None = None,
    error: str | None = None,
    state: str | None = None,
    conn: asyncpg.Connection = Depends(db),
    vb_oauth_state: str | None = Cookie(default=None),
    vb_oauth_verifier: str | None = Cookie(default=None),
    vb_oauth_next: str | None = Cookie(default=None),
):
    landing = f"{settings.public_base_url}{auth.safe_next(vb_oauth_next)}"

    def failed(message: str) -> RedirectResponse:
        out = RedirectResponse(
            f"{settings.public_base_url}{SIGNIN_PATH}?auth_error={quote(message)}",
            status_code=302,
        )
        for name in (auth.STATE_COOKIE, auth.VERIFIER_COOKIE, auth.NEXT_COOKIE):
            out.delete_cookie(name, path="/")
        return out

    if error:  # the user pressed Cancel, or Google refused
        return failed("Sign-in was cancelled.")
    if not code:
        return failed("Google did not return an authorization code.")

    try:
        auth.check_state(state, vb_oauth_state)
        if not vb_oauth_verifier:
            raise auth.AuthError("Your browser did not keep the sign-in cookie. Check that "
                                 "cookies are enabled and try again.")
        tokens = await auth.exchange_code(code, vb_oauth_verifier)
        claims = auth.verify_id_token(tokens.get("id_token", ""))
        user = await auth.upsert_user(conn, claims)
        token = await auth.create_session(conn, user["id"], request.headers.get("user-agent"))
    except auth.AuthError as e:
        log.warning("google sign-in failed: %s", e)
        return failed(str(e))

    response = RedirectResponse(landing, status_code=302)
    _set_cookie(response, auth.SESSION_COOKIE, token, settings.session_days * 86400)
    for name in (auth.STATE_COOKIE, auth.VERIFIER_COOKIE, auth.NEXT_COOKIE):
        response.delete_cookie(name, path="/")
    return response


@app.post("/auth/signout", status_code=204)
async def signout(
    response: Response,
    conn: asyncpg.Connection = Depends(db),
    vb_session: str | None = Cookie(default=None),
):
    """Deletes the row, not just the cookie — a copy of the token taken from elsewhere
    stops working too."""
    if vb_session:
        await auth.revoke_session(conn, vb_session)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")


@app.get("/api/me")
async def me(user=Depends(current_user)):
    return {
        "id": str(user["id"]),
        "email": user["email"],
        "name": user["name"] if "name" in user.keys() else None,
        "avatar_url": user["avatar_url"] if "avatar_url" in user.keys() else None,
    }


async def owned_space(space_id: UUID, conn: asyncpg.Connection, user: asyncpg.Record):
    """The tenancy boundary. Every read and write of Space-owned data routes through here.

    404, not 403 — a caller must not be able to probe which Space ids exist.
    """
    space = await conn.fetchrow(
        "select * from spaces where id = $1 and user_id = $2 and deleted_at is null",
        space_id, user["id"],
    )
    if space is None:
        raise HTTPException(404, "Space not found")
    return space


async def owned_document(document_id: UUID, conn: asyncpg.Connection, user: asyncpg.Record):
    doc = await conn.fetchrow(
        """select d.* from documents d
             join spaces s on s.id = d.space_id
            where d.id = $1 and s.user_id = $2 and s.deleted_at is null""",
        document_id, user["id"],
    )
    if doc is None:
        raise HTTPException(404, "Document not found")
    return doc


async def owned_conversation(conversation_id: UUID, conn: asyncpg.Connection, user: asyncpg.Record):
    row = await conn.fetchrow(
        """select c.*, s.id as space_id from conversations c
             join spaces s on s.id = c.space_id
            where c.id = $1 and s.user_id = $2 and s.deleted_at is null""",
        conversation_id, user["id"],
    )
    if row is None:
        raise HTTPException(404, "Conversation not found")
    return row


# ------------------------------------------------------------------ health


@app.get("/health")
async def health(conn: asyncpg.Connection = Depends(db)):
    queued = await conn.fetchval("select count(*) from documents where status = 'queued'")
    return {"ok": True, "queued_documents": queued, "model": settings.gemini_model}


# ------------------------------------------------------------------ spaces


@app.get("/api/spaces")
async def list_spaces(conn=Depends(db), user=Depends(current_user)):
    rows = await conn.fetch(
        # Scalar subqueries, not joins. Joining documents *and* messages fans the rows out,
        # and count(d.id) then reports documents x messages — a Space with two files and
        # nineteen questions asked of them displayed as "38 documents".
        """select s.id, s.name, s.description, s.created_at,
                  (select count(*) from documents d where d.space_id = s.id) as document_count,
                  greatest(
                      (select max(d.created_at) from documents d where d.space_id = s.id),
                      (select max(m.created_at) from messages m
                         join conversations cv on cv.id = m.conversation_id
                        where cv.space_id = s.id)
                  ) as last_activity_at
             from spaces s
            where s.user_id = $1 and s.deleted_at is null
            order by 6 desc nulls last, s.created_at desc""",
        user["id"],
    )
    return [_space_json(r) for r in rows]


@app.post("/api/spaces", status_code=201)
async def create_space(body: SpaceIn, conn=Depends(db), user=Depends(current_user)):
    row = await conn.fetchrow(
        "insert into spaces (user_id, name, description) values ($1, $2, $3) returning *",
        user["id"], body.name, body.description,
    )
    return {"id": str(row["id"]), "name": row["name"], "description": row["description"],
            "document_count": 0, "last_activity_at": None}


@app.get("/api/spaces/{space_id}")
async def get_space(space_id: UUID, conn=Depends(db), user=Depends(current_user)):
    await owned_space(space_id, conn, user)
    row = await conn.fetchrow(
        """select s.id, s.name, s.description,
                  (select count(*) from documents d where d.space_id = s.id) as document_count,
                  (select max(d.created_at) from documents d where d.space_id = s.id)
                      as last_activity_at
             from spaces s where s.id = $1""",
        space_id,
    )
    return _space_json(row)


@app.patch("/api/spaces/{space_id}")
async def patch_space(space_id: UUID, body: SpacePatch, conn=Depends(db), user=Depends(current_user)):
    space = await owned_space(space_id, conn, user)
    row = await conn.fetchrow(
        "update spaces set name = coalesce($2, name), description = coalesce($3, description) "
        "where id = $1 returning *",
        space["id"], body.name, body.description,
    )
    return {"id": str(row["id"]), "name": row["name"], "description": row["description"]}


@app.delete("/api/spaces/{space_id}", status_code=204)
async def delete_space(space_id: UUID, conn=Depends(db), user=Depends(current_user)):
    space = await owned_space(space_id, conn, user)
    await conn.execute("update spaces set deleted_at = now() where id = $1", space["id"])


def _space_json(r):
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "description": r["description"],
        "document_count": r["document_count"],
        "last_activity_at": r["last_activity_at"].isoformat() if r["last_activity_at"] else None,
    }


# ------------------------------------------------------------------ documents


@app.get("/api/spaces/{space_id}/documents")
async def list_documents(space_id: UUID, conn=Depends(db), user=Depends(current_user)):
    await owned_space(space_id, conn, user)
    rows = await conn.fetch(
        """select d.*, (select count(*) from chunks c where c.document_id = d.id) as indexed
             from documents d where d.space_id = $1 order by d.created_at""",
        space_id,
    )
    return [_document_json(r) for r in rows]


@app.post("/api/spaces/{space_id}/documents", status_code=202)
async def upload_document(
    space_id: UUID, file: UploadFile = File(...), conn=Depends(db), user=Depends(current_user)
):
    """M1 deviation from PRD FR-6: the file is posted here rather than PUT to a presigned
    URL. Swap in presigning when storage moves off local disk; the client contract is a
    POST either way."""
    await owned_space(space_id, conn, user)

    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(413, f"File exceeds {settings.max_upload_bytes // 1_000_000} MB")

    digest = hashlib.sha256(data).hexdigest()
    existing = await conn.fetchrow(
        "select * from documents where space_id = $1 and sha256 = $2", space_id, digest
    )
    if existing:
        return _document_json(existing) | {"duplicate": True}

    ingest.store(digest, data)
    row = await conn.fetchrow(
        '''insert into documents (space_id, filename, mime_type, size_bytes, sha256, storage_key)
           values ($1, $2, $3, $4, $5, $6) returning *''',
        space_id, file.filename or "untitled",
        file.content_type or "application/octet-stream", len(data), digest, digest,
    )
    return _document_json(row)


@app.post("/api/documents/{document_id}/retry", status_code=202)
async def retry_document(document_id: UUID, conn=Depends(db), user=Depends(current_user)):
    """Try this document now.

    Covers both cases a user hits: a document written off as failed, and one queued behind
    a provider wait that has since cleared. Refused only while it is already in flight.
    """
    doc = await owned_document(document_id, conn, user)
    if doc["status"] in ("parsing", "embedding"):
        raise HTTPException(409, "Already being ingested")
    if doc["status"] == "ready":
        raise HTTPException(409, "Already ingested")
    row = await conn.fetchrow(
        """update documents
              set status = 'queued', attempts = 0, error = null, retry_after = null
            where id = $1 returning *""",
        doc["id"],
    )
    return _document_json(row)


@app.delete("/api/documents/{document_id}", status_code=204)
async def delete_document(document_id: UUID, conn=Depends(db), user=Depends(current_user)):
    doc = await owned_document(document_id, conn, user)
    async with conn.transaction():
        await conn.execute("delete from documents where id = $1", doc["id"])  # chunks cascade
        # Storage is content-addressed, so identical bytes in two Spaces are one file on
        # disk. Remove it only once nothing points at it — otherwise deleting one Space's
        # copy silently breaks another's.
        shared = await conn.fetchval(
            "select exists (select 1 from documents where storage_key = $1)", doc["storage_key"]
        )
    if not shared:
        ingest.discard(doc["storage_key"])


def _document_json(r):
    return {
        "id": str(r["id"]),
        "space_id": str(r["space_id"]),
        "filename": r["filename"],
        "size_bytes": r["size_bytes"],
        "page_count": r["page_count"],
        "status": r["status"],
        "error": r["error"],
        # When the worker will pick it up again. The UI renders it in the viewer's timezone,
        # because "it will retry" without a time reads as "it is stuck".
        "retry_after": r["retry_after"].isoformat() if r.get("retry_after") else None,
        # Embedding is resumable, so a document waiting on quota has usually banked real
        # progress. Without this, "Queued" looks identical to "stuck".
        "indexed_chunks": r["indexed"] if "indexed" in r.keys() else None,
    }


# ------------------------------------------------------------------ chunks


@app.get("/api/chunks/{chunk_id}")
async def get_chunk(chunk_id: UUID, conn=Depends(db), user=Depends(current_user)):
    row = await conn.fetchrow(
        """select c.id, c.text, c.page_start, c.page_end, c.char_start, c.char_end,
                  d.id as document_id, d.filename
             from chunks c
             join documents d on d.id = c.document_id
             join spaces s on s.id = c.space_id
            where c.id = $1 and s.user_id = $2 and s.deleted_at is null""",
        chunk_id, user["id"],
    )
    if row is None:
        raise HTTPException(404, "Chunk not found")
    return {
        "chunk_id": str(row["id"]),
        "document_id": str(row["document_id"]),
        "document_name": row["filename"],
        "page": row["page_start"],
        "context": row["text"],
        "char_start": row["char_start"],
        "char_end": row["char_end"],
    }


# ------------------------------------------------------------------ conversations & chat


@app.get("/api/spaces/{space_id}/conversations")
async def list_conversations(space_id: UUID, conn=Depends(db), user=Depends(current_user)):
    await owned_space(space_id, conn, user)
    rows = await conn.fetch(
        "select id, title, created_at from conversations where space_id = $1 "
        "order by created_at desc",
        space_id,
    )
    return [{"id": str(r["id"]), "title": r["title"],
             "created_at": r["created_at"].isoformat()} for r in rows]


@app.post("/api/spaces/{space_id}/conversations", status_code=201)
async def create_conversation(space_id: UUID, conn=Depends(db), user=Depends(current_user)):
    await owned_space(space_id, conn, user)
    row = await conn.fetchrow(
        "insert into conversations (space_id) values ($1) returning id, created_at", space_id
    )
    return {"id": str(row["id"]), "title": None, "created_at": row["created_at"].isoformat()}


@app.get("/api/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: UUID, conn=Depends(db), user=Depends(current_user)):
    await owned_conversation(conversation_id, conn, user)
    rows = await conn.fetch(
        "select * from messages where conversation_id = $1 order by created_at", conversation_id
    )
    return [{"id": str(r["id"]), "role": r["role"], "content": r["content"],
             "citations": r["citations"]} for r in rows]


@app.post("/api/conversations/{conversation_id}/messages")
async def ask(
    conversation_id: UUID,
    body: AskIn,
    request: Request,
    conn=Depends(db),
    user=Depends(current_user),
):
    """Server-sent events: `search`, `token`, `citation`, `done`, `error`."""
    convo = await owned_conversation(conversation_id, conn, user)
    space_id = convo["space_id"]
    history = await _history(conn, conversation_id)

    async def events():
        # This generator outlives the request-scoped connection, so it takes its own.
        async with acquire() as stream_conn:
            await stream_conn.execute(
                "insert into messages (conversation_id, role, content) values ($1, 'user', $2)",
                conversation_id, body.content,
            )
            if convo["title"] is None:
                await stream_conn.execute(
                    "update conversations set title = $2 where id = $1",
                    conversation_id, body.content[:80],
                )
            try:
                async for event in answer(
                    stream_conn, space_id, body.content,
                    history=history, document_ids=body.document_ids,
                ):
                    if await request.is_disconnected():
                        return
                    if event["type"] == "done":
                        row = await stream_conn.fetchrow(
                            """insert into messages (conversation_id, role, content, citations,
                                                     searches, ungrounded, dropped_citations,
                                                     model, latency_ms)
                               values ($1, 'assistant', $2, $3, $4, $5, $6, $7, $8)
                               returning id""",
                            conversation_id, event["answer"], event["citations"],
                            event["searches"], event["ungrounded"],
                            event["dropped_citations"], settings.gemini_model,
                            event["latency_ms"],
                        )
                        event = event | {"message_id": str(row["id"])}
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception as e:  # noqa: BLE001 — the stream is the only channel we have
                log.exception("answer failed")
                yield f'data: {json.dumps({"type": "error", "message": friendly_error(e)})}\n\n'

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _history(conn, conversation_id: UUID, turns: int = 8) -> list[types.Content]:
    """Recent turns, as plain text. Prior tool calls are deliberately not replayed — they
    were retrieval for a different question, and replaying them crowds out this one."""
    rows = await conn.fetch(
        "select role, content from messages where conversation_id = $1 "
        "order by created_at desc limit $2",
        conversation_id, turns,
    )
    return [
        types.Content(
            role="user" if r["role"] == "user" else "model",
            parts=[types.Part.from_text(text=r["content"])],
        )
        for r in reversed(rows)
    ]
