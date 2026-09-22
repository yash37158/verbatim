"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { LogOut } from "lucide-react";
import type { User } from "@/lib/session";
import { cn } from "@/components/ui";

function initials(user: User) {
  const source = user.name?.trim() || user.email;
  const parts = source.split(/[\s@._-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
}

export function UserMenu({ user }: { user: User }) {
  const [open, setOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const router = useRouter();

  async function signOut() {
    setSigningOut(true);
    // The server deletes the session row; clearing the cookie alone would leave a copied
    // token working.
    await fetch("/api/backend/auth/signout", { method: "POST", credentials: "include" });
    router.push("/");
    router.refresh();
  }

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Account: ${user.email}`}
        className="flex items-center gap-2 rounded-full p-0.5 pr-2 text-sm text-muted transition-colors hover:bg-sunken"
      >
        {user.avatar_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- a Google avatar URL, not a bundled asset
          <img src={user.avatar_url} alt="" className="size-7 rounded-full" />
        ) : (
          <span className="grid size-7 place-items-center rounded-full border border-line bg-sunken text-[11px] font-semibold text-ink">
            {initials(user)}
          </span>
        )}
        <span className="hidden max-w-[12rem] truncate sm:inline">
          {user.name || user.email}
        </span>
      </button>

      {open && (
        <>
          {/* Click-away. A plain overlay beats a document listener: it disappears with the menu. */}
          <button
            type="button"
            aria-label="Close menu"
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-40 cursor-default"
          />
          <div
            role="menu"
            className="absolute right-0 top-full z-50 mt-1.5 w-60 overflow-hidden rounded-xl border border-line bg-surface shadow-lg"
          >
            <div className="border-b border-line px-3 py-2.5">
              {user.name && <p className="truncate text-[13px] font-medium">{user.name}</p>}
              <p className="truncate text-[12px] text-muted">{user.email}</p>
            </div>
            <button
              type="button"
              role="menuitem"
              onClick={signOut}
              disabled={signingOut}
              className={cn(
                "flex w-full items-center gap-2 px-3 py-2.5 text-left text-[13px] transition-colors hover:bg-sunken",
                signingOut && "opacity-50",
              )}
            >
              <LogOut className="size-3.5" />
              {signingOut ? "Signing out…" : "Sign out"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
