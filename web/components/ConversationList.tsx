"use client";

import { useState } from "react";
import { Check, MessageSquare, Pencil, Plus, Trash2, X } from "lucide-react";
import type { Conversation } from "@/lib/types";
import { cn, formatWhen } from "@/components/ui";

/**
 * The Space's chat history. Everything here was already stored and auto-titled by the
 * backend; until now the UI could only ever show the conversation it was in the middle of.
 */
export function ConversationList({
  conversations,
  activeId,
  onSelect,
  onNew,
  onRename,
  onDelete,
}: {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onRename: (id: string, title: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState<{ id: string; title: string } | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);

  async function commitRename() {
    if (!editing) return;
    const title = editing.title.trim();
    setEditing(null);
    if (title) await onRename(editing.id, title);
  }

  return (
    <div className="flex flex-col border-b border-line">
      <div className="flex items-center justify-between px-4 pb-2 pt-4">
        <h2 className="text-[11px] font-semibold uppercase tracking-wide text-faint">Chats</h2>
        <button
          type="button"
          onClick={onNew}
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] font-medium text-muted transition-colors hover:bg-sunken hover:text-ink"
        >
          <Plus className="size-3" />
          New chat
        </button>
      </div>

      {conversations.length === 0 ? (
        <p className="px-4 pb-3 text-[12px] text-faint">Nothing asked yet.</p>
      ) : (
        <ul className="max-h-[38vh] space-y-0.5 overflow-y-auto px-2 pb-2">
          {conversations.map((c) => {
            const active = c.id === activeId;
            const isEditing = editing?.id === c.id;
            const isConfirming = confirming === c.id;
            return (
              <li key={c.id} className="group">
                {isEditing ? (
                  <form
                    onSubmit={(e) => {
                      e.preventDefault();
                      void commitRename();
                    }}
                    className="flex items-center gap-1 rounded-lg bg-sunken p-1.5"
                  >
                    <input
                      autoFocus
                      value={editing.title}
                      maxLength={200}
                      onChange={(e) => setEditing({ id: c.id, title: e.target.value })}
                      onKeyDown={(e) => e.key === "Escape" && setEditing(null)}
                      className="min-w-0 flex-1 rounded border border-line bg-surface px-1.5 py-1 text-[13px] outline-none focus:border-faint"
                      aria-label="Conversation title"
                    />
                    <button type="submit" aria-label="Save title" className="rounded p-1 hover:bg-line">
                      <Check className="size-3.5" />
                    </button>
                    <button type="button" aria-label="Cancel" onClick={() => setEditing(null)} className="rounded p-1 hover:bg-line">
                      <X className="size-3.5" />
                    </button>
                  </form>
                ) : isConfirming ? (
                  <div className="flex items-center justify-between gap-2 rounded-lg bg-sunken px-2.5 py-2 text-[12px]">
                    <span className="text-bad">Delete this chat?</span>
                    <span className="flex gap-1">
                      <button
                        type="button"
                        onClick={async () => {
                          setConfirming(null);
                          await onDelete(c.id);
                        }}
                        className="rounded border border-bad px-2 py-0.5 font-medium text-bad hover:bg-bad hover:text-canvas"
                      >
                        Delete
                      </button>
                      <button type="button" onClick={() => setConfirming(null)} className="rounded px-2 py-0.5 hover:bg-line">
                        Keep
                      </button>
                    </span>
                  </div>
                ) : (
                  <div
                    className={cn(
                      "flex items-start gap-2 rounded-lg px-2.5 py-2 transition-colors",
                      active ? "bg-sunken" : "hover:bg-sunken/60",
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => onSelect(c.id)}
                      aria-current={active ? "true" : undefined}
                      className="min-w-0 flex-1 text-left"
                    >
                      <span className="flex items-center gap-1.5">
                        <MessageSquare className="size-3.5 shrink-0 text-faint" />
                        <span className={cn("truncate text-[13px]", !c.title && "italic text-muted")}>
                          {c.title ?? "Untitled chat"}
                        </span>
                      </span>
                      <span className="mt-0.5 block pl-5 text-[11px] text-faint">
                        {formatWhen(c.last_message_at)}
                        {c.message_count > 0 && ` · ${Math.ceil(c.message_count / 2)} ${c.message_count > 2 ? "questions" : "question"}`}
                      </span>
                    </button>
                    <span className="flex shrink-0 gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
                      <button
                        type="button"
                        aria-label="Rename chat"
                        onClick={() => setEditing({ id: c.id, title: c.title ?? "" })}
                        className="rounded p-1 text-faint hover:bg-line hover:text-ink"
                      >
                        <Pencil className="size-3" />
                      </button>
                      <button
                        type="button"
                        aria-label="Delete chat"
                        onClick={() => setConfirming(c.id)}
                        className="rounded p-1 text-faint hover:bg-line hover:text-bad"
                      >
                        <Trash2 className="size-3" />
                      </button>
                    </span>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
