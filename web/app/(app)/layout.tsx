import Link from "next/link";
import { redirect } from "next/navigation";
import { currentUser } from "@/lib/session";
import { UserMenu } from "@/components/UserMenu";
import { Wordmark } from "@/components/ui";

/**
 * Chrome for the signed-in app, and the place the session is actually verified.
 *
 * Middleware bounces visitors with no cookie at all, but a cookie can be forged, revoked or
 * expired. This asks the API who it belongs to and renders nothing until it has an answer.
 */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const user = await currentUser();
  if (!user) redirect("/signin");

  return (
    <>
      <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-line bg-canvas/85 px-5 backdrop-blur">
        <Link href="/spaces" className="flex items-center gap-2">
          <Wordmark />
        </Link>
        <UserMenu user={user} />
      </header>
      <main className="h-[calc(100dvh-3.5rem)] overflow-hidden">{children}</main>
    </>
  );
}
