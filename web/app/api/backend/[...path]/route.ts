/**
 * Server-side proxy to the FastAPI backend.
 *
 * Same-origin, so the browser's session cookie reaches the API without CORS and without
 * any credential being visible to client JavaScript. The reply streams back untouched,
 * which matters for the SSE chat endpoint.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_URL = process.env.API_URL ?? "http://localhost:8000";

async function proxy(request: Request, path: string[]) {
  const url = new URL(request.url);
  const target = `${API_URL}/${path.join("/")}${url.search}`;

  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("content-length"); // the body is re-streamed; let fetch recompute it
  // The browser's session cookie rides along in `headers` and is the only credential.
  // There is no longer a shared token here: one existed, and it meant every visitor was
  // the same user.

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const upstream = await fetch(target, {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    // A proxy must hand redirects to the browser, not chase them itself. Following would
    // swallow the OAuth hop to Google and return Google's HTML from our own origin.
    redirect: "manual",
    // Node needs this to stream a request body rather than buffer it — large PDFs.
    ...(hasBody ? { duplex: "half" } : {}),
  } as RequestInit);

  const out = new Headers();
  upstream.headers.forEach((value, key) => {
    // Recomputed for the re-streamed body; set-cookie is handled separately below because
    // a Headers object collapses repeats into one comma-joined value, which corrupts them.
    if (key !== "content-encoding" && key !== "content-length" && key !== "set-cookie") {
      out.set(key, value);
    }
  });
  for (const cookie of upstream.headers.getSetCookie?.() ?? []) {
    out.append("set-cookie", cookie);
  }
  // 204/205/304 are "null body statuses": handing the Response constructor a body for one
  // aborts the request. Every delete endpoint and sign-out returns 204.
  const bodyless = upstream.status === 204 || upstream.status === 205 || upstream.status === 304;
  return new Response(bodyless ? null : upstream.body, {
    status: upstream.status,
    headers: out,
  });
}

// Next detects route handlers statically, so each verb needs its own declaration.
type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(request: Request, ctx: Ctx) {
  return proxy(request, (await ctx.params).path);
}
export async function POST(request: Request, ctx: Ctx) {
  return proxy(request, (await ctx.params).path);
}
export async function PATCH(request: Request, ctx: Ctx) {
  return proxy(request, (await ctx.params).path);
}
export async function DELETE(request: Request, ctx: Ctx) {
  return proxy(request, (await ctx.params).path);
}
