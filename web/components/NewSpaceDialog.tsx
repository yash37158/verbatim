"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus } from "lucide-react";
import { createSpace } from "@/lib/api";
import { Button } from "@/components/ui";

export function NewSpaceDialog() {
  // Native <dialog>: focus trap, Esc to close and the backdrop come with the element.
  const dialog = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const name = String(form.get("name") ?? "").trim();
    if (!name || busy) return;

    setBusy(true);
    setError(null);
    try {
      const space = await createSpace(name, String(form.get("description") ?? "").trim());
      dialog.current?.close();
      router.push(`/spaces/${space.id}`); // straight into the empty Space, ready for files
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <>
      <Button variant="primary" onClick={() => dialog.current?.showModal()}>
        <Plus className="size-4" />
        New Space
      </Button>

      <dialog
        ref={dialog}
        onClose={() => {
          setBusy(false);
          setError(null);
        }}
        className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-xl border border-line bg-surface p-0 text-ink shadow-2xl backdrop:bg-black/50 backdrop:backdrop-blur-[2px]"
      >
        <form onSubmit={submit} className="flex flex-col gap-4 p-5">
          <div>
            <h2 className="font-display text-lg tracking-tight">New Space</h2>
            <p className="mt-1 text-sm text-muted">
              A Space is a set of documents you ask questions across.
            </p>
          </div>

          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-faint">Name</span>
            <input
              name="name"
              required
              maxLength={200}
              autoFocus
              placeholder="Vendor Contracts"
              className="rounded-lg border border-line bg-transparent px-3 py-2 text-sm outline-none focus:border-faint"
            />
          </label>

          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-semibold uppercase tracking-wide text-faint">
              Description <span className="font-normal normal-case">(optional)</span>
            </span>
            <input
              name="description"
              maxLength={2000}
              placeholder="What lives in here?"
              className="rounded-lg border border-line bg-transparent px-3 py-2 text-sm outline-none focus:border-faint"
            />
          </label>

          {error && <p className="text-[12px] text-bad">{error}</p>}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => dialog.current?.close()}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" disabled={busy}>
              {busy ? "Creating…" : "Create Space"}
            </Button>
          </div>
        </form>
      </dialog>
    </>
  );
}
