import Link from "next/link";
import { redirect } from "next/navigation";
import { AlertCircle, ArrowLeft } from "lucide-react";
import { currentUser } from "@/lib/session";
import { Wordmark } from "@/components/ui";

/** Google's mark, inline so the button never waits on a network round trip to render. */
function GoogleMark() {
  return (
    <svg viewBox="0 0 48 48" className="size-[18px]" aria-hidden>
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

export default async function SignIn({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; auth_error?: string }>;
}) {
  const { next, auth_error } = await searchParams;

  // Only same-site paths. An unchecked `next` here would hand an attacker a link that signs
  // you in and then lands you somewhere hostile carrying our name.
  const destination = next?.startsWith("/") && !next.startsWith("//") ? next : "/spaces";

  if (await currentUser()) redirect(destination);

  const start = `/api/backend/auth/google?next=${encodeURIComponent(destination)}`;

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="flex h-14 items-center px-5">
        <Link href="/" className="inline-flex items-center gap-2 text-muted transition-colors hover:text-ink">
          <ArrowLeft className="size-4" />
          <Wordmark className="text-ink" />
        </Link>
      </header>

      <main className="flex flex-1 items-center justify-center px-5 pb-24">
        <div className="w-full max-w-sm">
          <h1 className="font-display text-2xl tracking-tight">Sign in to Verbatim</h1>
          <p className="mt-2 text-[15px] leading-relaxed text-muted">
            Your Spaces and documents are private to your account.
          </p>

          {auth_error && (
            <p
              role="alert"
              className="mt-5 flex items-start gap-2 rounded-lg border border-line bg-surface px-3 py-2.5 text-[13px] leading-snug text-bad"
            >
              <AlertCircle className="mt-0.5 size-4 shrink-0" />
              {auth_error}
            </p>
          )}

          <a
            href={start}
            className="mt-6 flex w-full items-center justify-center gap-3 rounded-lg border border-line bg-surface px-4 py-3 text-sm font-medium transition-colors hover:bg-sunken"
          >
            <GoogleMark />
            Continue with Google
          </a>

          <p className="mt-6 text-[12px] leading-relaxed text-faint">
            We ask Google only for your name, email address and profile picture. Verbatim
            never sees your Google password.
          </p>
        </div>
      </main>
    </div>
  );
}
