import { NextResponse, type NextRequest } from "next/server";

/**
 * A cheap, path-aware bounce for signed-out visitors.
 *
 * It only checks that a session cookie *exists*. Deciding whether one is valid means asking
 * the API, and doing that on every request — including each static asset — would put a
 * network round trip in front of the whole app. The real check lives in the (app) layout,
 * which validates the session server-side before rendering anything.
 *
 * So: middleware knows the path and can preserve it in `next`; the layout knows the truth.
 */
export function middleware(request: NextRequest) {
  if (request.cookies.has("vb_session")) return NextResponse.next();

  const signin = new URL("/signin", request.url);
  signin.searchParams.set("next", request.nextUrl.pathname + request.nextUrl.search);
  return NextResponse.redirect(signin);
}

export const config = {
  matcher: ["/spaces/:path*"],
};
