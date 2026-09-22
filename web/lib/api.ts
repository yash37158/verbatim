// The only place that knows whether we are talking to FastAPI or to mocks.
// Set NEXT_PUBLIC_API_URL and every call below switches over; nothing else changes.
import { drainSSE } from "./sse";
import * as mock from "./mock";
import type { Doc, Space, StreamEvent } from "./types";

// One switch, two targets. Server components call FastAPI directly — a relative URL is
// not fetchable from Node, and bouncing off our own proxy would be a pointless round trip.
// The browser goes through /api/backend so the token never leaves the server.
const LIVE = Boolean(process.env.NEXT_PUBLIC_API_URL);
const onServer = typeof window === "undefined";
const BASE = onServer ? (process.env.API_URL ?? "") : (process.env.NEXT_PUBLIC_API_URL ?? "");

/**
 * Whose request is this?
 *
 * In the browser, nothing: the session cookie is same-origin and `credentials: "include"`
 * sends it, and the proxy forwards it on. On the server there is no ambient browser, so the
 * cookie is read from the incoming request and attached by hand.
 *
 * `next/headers` is server-only, hence the dynamic import behind the guard — a static one
 * would follow this module into the client bundle.
 */
async function authHeaders(): Promise<HeadersInit> {
  if (!onServer) return {};
  const { cookies } = await import("next/headers");
  const token = (await cookies()).get("vb_session")?.value;
  return token ? { cookie: `vb_session=${token}` } : {};
}

/**
 * A 401 means the session ended — expired, revoked, or signed out in another tab. Sending
 * the user to sign in beats surfacing "401 Unauthorized" as though it were a bug in the page.
 * On the server, redirecting is the layout's job, so this just throws.
 */
function ended(): never {
  if (!onServer) window.location.href = `/signin?next=${encodeURIComponent(location.pathname)}`;
  throw new Error("Not signed in");
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    headers: await authHeaders(),
    cache: "no-store",
  });
  if (res.status === 401) ended();
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function listSpaces(): Promise<Space[]> {
  return LIVE ? get("/api/spaces") : mock.spaces;
}

export async function getSpace(id: string): Promise<Space | undefined> {
  return LIVE ? get(`/api/spaces/${id}`) : mock.spaces.find((s) => s.id === id);
}

export async function createSpace(name: string, description?: string): Promise<Space> {
  if (!LIVE) return mock.createSpace(name, description);
  const res = await fetch(`${BASE}/api/spaces`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(await authHeaders()) },
    body: JSON.stringify({ name, description: description || null }),
  });
  if (res.status === 401) ended();
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function uploadDocument(spaceId: string, file: File): Promise<Doc> {
  if (!LIVE) return mock.uploadDocument(spaceId, file);
  const body = new FormData();
  body.append("file", file);
  const res = await fetch(`${BASE}/api/spaces/${spaceId}/documents`, {
    method: "POST",
    credentials: "include",
    headers: await authHeaders(), // no Content-Type: the browser sets the multipart boundary
    body,
  });
  if (res.status === 401) ended();
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export async function retryDocument(documentId: string): Promise<Doc> {
  if (!LIVE) return mock.retryDocument(documentId);
  const res = await fetch(`${BASE}/api/documents/${documentId}/retry`, {
    method: "POST",
    credentials: "include",
    headers: await authHeaders(),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export async function listDocuments(spaceId: string): Promise<Doc[]> {
  return LIVE ? get(`/api/spaces/${spaceId}/documents`) : (mock.docs[spaceId] ?? []);
}

export async function createConversation(spaceId: string): Promise<{ id: string }> {
  if (!LIVE) return { id: "c_mock" };
  const res = await fetch(`${BASE}/api/spaces/${spaceId}/conversations`, {
    method: "POST",
    credentials: "include",
    headers: await authHeaders(),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

/**
 * Streams one answer. Both arms yield the same events, so the chat UI never
 * learns which backend it is talking to.
 */
export async function* streamAnswer(
  conversationId: string,
  question: string,
  documentIds?: string[],
): AsyncGenerator<StreamEvent> {
  if (!LIVE) {
    yield* mock.streamMockAnswer(question);
    return;
  }

  const res = await fetch(`${BASE}/api/conversations/${conversationId}/messages`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream", ...(await authHeaders()) },
    body: JSON.stringify({ content: question, document_ids: documentIds }),
  });
  if (res.status === 401) ended();
  if (!res.ok || !res.body) {
    yield { type: "error", message: `${res.status} ${res.statusText}` };
    return;
  }

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    const { events, rest } = drainSSE(buffer + value);
    buffer = rest;
    for (const e of events) yield e as StreamEvent;
  }
}
