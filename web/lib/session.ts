import { cookies } from "next/headers";

/**
 * Server-only. Reads the session cookie and asks the API who it belongs to.
 *
 * Deliberately not in lib/api.ts: that file is imported by client components, and
 * `next/headers` only exists on the server. Keeping them apart means the boundary is a
 * file, not a convention someone has to remember.
 */

export const SESSION_COOKIE = "vb_session";

export interface User {
  id: string;
  email: string;
  name: string | null;
  avatar_url: string | null;
}

export async function currentUser(): Promise<User | null> {
  const token = (await cookies()).get(SESSION_COOKIE)?.value;
  if (!token) return null;

  const base = process.env.API_URL ?? "http://localhost:8000";
  try {
    const res = await fetch(`${base}/api/me`, {
      headers: { cookie: `${SESSION_COOKIE}=${token}` },
      cache: "no-store", // a signed-out user must never be served a cached signed-in page
    });
    return res.ok ? await res.json() : null;
  } catch {
    // The API being down is not the same as being signed out, but from here they are
    // indistinguishable, and treating it as signed-out fails closed.
    return null;
  }
}
