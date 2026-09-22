"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChevronLeft } from "lucide-react";
import { listDocuments, retryDocument, uploadDocument } from "@/lib/api";
import type { Citation, Doc, Space } from "@/lib/types";
import { ChatPane } from "@/components/ChatPane";
import { DocumentsPane } from "@/components/DocumentsPane";
import { SourceDrawer } from "@/components/SourceDrawer";
import { cn } from "@/components/ui";

export function Workspace({ space, documents }: { space: Space; documents: Doc[] }) {
  const [docs, setDocs] = useState(documents);
  const [selected, setSelected] = useState(
    () => new Set(documents.filter((d) => d.status === "ready").map((d) => d.id)),
  );
  const [citation, setCitation] = useState<Citation | null>(null);
  const [showDocs, setShowDocs] = useState(false);

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.delete(id) || next.add(id);
      return next;
    });
  }

  async function upload(files: File[]) {
    for (const file of files) {
      // An optimistic row so the file appears the instant it is dropped; the server's
      // row replaces it, carrying the real id the status poll then tracks.
      const placeholder: Doc = {
        id: `pending_${crypto.randomUUID()}`,
        space_id: space.id,
        filename: file.name,
        size_bytes: file.size,
        page_count: null,
        status: "queued",
      };
      setDocs((d) => [...d, placeholder]);
      try {
        const created = await uploadDocument(space.id, file);
        setDocs((d) => {
          const without = d.filter((x) => x.id !== placeholder.id && x.id !== created.id);
          return [...without, created]; // re-uploading the same bytes returns the existing row
        });
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e);
        setDocs((d) =>
          d.map((x) => (x.id === placeholder.id ? { ...x, status: "failed", error: message } : x)),
        );
      }
    }
  }

  async function retry(id: string) {
    setDocs((d) => d.map((x) => (x.id === id ? { ...x, status: "queued", error: undefined } : x)));
    try {
      const updated = await retryDocument(id);
      setDocs((d) => d.map((x) => (x.id === id ? updated : x)));
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      setDocs((d) => d.map((x) => (x.id === id ? { ...x, status: "failed", error: message } : x)));
    }
  }

  // Ingestion is asynchronous, so the statuses have to be pulled. Polling only runs while
  // something is actually in flight — an idle Space makes no requests.
  const settling = docs.some((d) => d.status !== "ready" && d.status !== "failed");
  useEffect(() => {
    if (!settling) return;
    const timer = setInterval(async () => {
      try {
        const fresh = await listDocuments(space.id);
        setDocs((prev) => {
          const known = new Set(fresh.map((d) => d.id));
          const notYetOnServer = prev.filter((d) => d.id.startsWith("pending_") && !known.has(d.id));
          return [...fresh, ...notYetOnServer];
        });
      } catch {
        // A blip on one poll is not worth surfacing; the next tick retries.
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [settling, space.id]);

  // Bring newly searchable documents into scope once each, so a box the user deliberately
  // unchecked does not tick itself back on at the next poll.
  const autoSelected = useRef(
    new Set(documents.filter((d) => d.status === "ready").map((d) => d.id)),
  );
  useEffect(() => {
    const arrived = docs.filter((d) => d.status === "ready" && !autoSelected.current.has(d.id));
    if (!arrived.length) return;
    arrived.forEach((d) => autoSelected.current.add(d.id));
    setSelected((prev) => new Set([...prev, ...arrived.map((d) => d.id)]));
  }, [docs]);

  return (
    <div className="flex h-full">
      <div
        className={cn(
          "fixed bottom-0 left-0 top-14 z-30 w-72 border-r border-line transition-transform",
          "lg:static lg:z-auto lg:translate-x-0",
          showDocs ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-12 items-center border-b border-line bg-surface px-2">
          <Link
            href="/spaces"
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[13px] text-muted transition-colors hover:bg-sunken hover:text-ink"
          >
            <ChevronLeft className="size-3.5" />
            All Spaces
          </Link>
        </div>
        <DocumentsPane
          documents={docs}
          selected={selected}
          onToggle={toggle}
          onUpload={upload}
          onRetry={retry}
          className="h-[calc(100%-3rem)]"
        />
      </div>

      {showDocs && (
        <button
          type="button"
          aria-label="Close documents panel"
          onClick={() => setShowDocs(false)}
          className="fixed inset-0 z-20 bg-black/25 lg:hidden"
        />
      )}

      <ChatPane
        spaceId={space.id}
        spaceName={space.name}
        selectedIds={[...selected]}
        onCite={setCitation}
        onToggleDocs={() => setShowDocs((v) => !v)}
        className="flex-1"
      />

      {citation && <SourceDrawer citation={citation} onClose={() => setCitation(null)} />}
    </div>
  );
}
