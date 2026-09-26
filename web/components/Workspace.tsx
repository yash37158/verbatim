"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { ChevronLeft } from "lucide-react";
import {
  deleteConversation,
  listConversations,
  listDocuments,
  listMessages,
  renameConversation,
  retryDocument,
  uploadDocument,
} from "@/lib/api";
import type { Citation, Conversation, Doc, Message, Space } from "@/lib/types";
import { ChatPane } from "@/components/ChatPane";
import { ConversationList } from "@/components/ConversationList";
import { DocumentsPane } from "@/components/DocumentsPane";
import { SourceDrawer } from "@/components/SourceDrawer";
import { cn } from "@/components/ui";

export function Workspace({
  space,
  documents,
  conversations: initialConversations,
  activeConversation,
  initialMessages,
}: {
  space: Space;
  documents: Doc[];
  conversations: Conversation[];
  /** From `?c=` — the page preloads its messages server-side so a reload restores it. */
  activeConversation: Conversation | null;
  initialMessages: Message[];
}) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [docs, setDocs] = useState(documents);
  const [conversations, setConversations] = useState(initialConversations);
  const [active, setActive] = useState<{ id: string | null; messages: Message[] }>({
    id: activeConversation?.id ?? null,
    messages: initialMessages,
  });

  function setUrl(id: string | null) {
    const params = new URLSearchParams(searchParams.toString());
    if (id) params.set("c", id);
    else params.delete("c");
    const qs = params.toString();
    router.replace(`/spaces/${space.id}${qs ? `?${qs}` : ""}`, { scroll: false });
  }

  async function openConversation(id: string) {
    if (id === active.id) return;
    try {
      const messages = await listMessages(id);
      setCitation(null); // a source panel from the thread being left should not linger
      setActive({ id, messages });
      setUrl(id);
    } catch {
      // The list refresh below will drop it if it is gone.
      setConversations(await listConversationsSafe());
    }
  }

  function newChat() {
    setCitation(null);
    setActive({ id: null, messages: [] });
    setUrl(null);
  }

  function conversationCreated(c: Conversation) {
    setConversations((list) => [c, ...list]);
    setActive((a) => ({ ...a, id: c.id }));
    setUrl(c.id);
  }

  async function rename(id: string, title: string) {
    const updated = await renameConversation(id, title);
    setConversations((list) => list.map((c) => (c.id === id ? { ...c, ...updated } : c)));
  }

  async function remove(id: string) {
    await deleteConversation(id);
    setConversations((list) => list.filter((c) => c.id !== id));
    if (active.id === id) newChat();
  }

  async function listConversationsSafe(): Promise<Conversation[]> {
    try {
      return await listConversations(space.id);
    } catch {
      return conversations; // a blip on one refresh is not worth losing the list over
    }
  }

  // Titles are assigned by the backend on the first question; pull the list once the first
  // exchange finishes so the sidebar shows the real title instead of "Untitled chat".
  useEffect(() => {
    const untitled = conversations.find((c) => c.id === active.id && !c.title);
    if (!untitled) return;
    const t = setTimeout(async () => setConversations(await listConversationsSafe()), 1500);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active.id, active.messages.length]);
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
  const settling = docs.some(
    (d) =>
      (d.status !== "ready" && d.status !== "failed") ||
      (d.status === "ready" && d.total_chunks != null && d.indexed_chunks != null &&
        d.indexed_chunks < d.total_chunks),
  );
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
        <div className="flex h-[calc(100%-3rem)] flex-col bg-surface">
          <ConversationList
            conversations={conversations}
            activeId={active.id}
            onSelect={openConversation}
            onNew={newChat}
            onRename={rename}
            onDelete={remove}
          />
          <DocumentsPane
            documents={docs}
            selected={selected}
            onToggle={toggle}
            onUpload={upload}
            onRetry={retry}
            className="min-h-0 flex-1"
          />
        </div>
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
        // Keyed on the conversation: switching remounts the pane with that thread's
        // messages, which is simpler and safer than resetting state inside it.
        key={active.id ?? "new"}
        spaceId={space.id}
        spaceName={space.name}
        selectedIds={[...selected]}
        conversationId={active.id}
        initialMessages={active.messages}
        onConversationCreated={conversationCreated}
        onCite={setCitation}
        onToggleDocs={() => setShowDocs((v) => !v)}
        className="flex-1"
      />

      {citation && <SourceDrawer citation={citation} onClose={() => setCitation(null)} />}
    </div>
  );
}
