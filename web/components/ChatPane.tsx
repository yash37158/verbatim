"use client";

import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowUp, PanelLeft, Sparkles } from "lucide-react";
import { createConversation, streamAnswer } from "@/lib/api";
import type { Citation, Message } from "@/lib/types";
import { AnswerText } from "@/components/AnswerText";
import { Button, cn } from "@/components/ui";

const SUGGESTIONS = [
  "What are the termination terms?",
  "What happens to customer data when the contract ends?",
  "What is the pricing?",
];

export function ChatPane({
  spaceId,
  spaceName,
  selectedIds,
  onCite,
  onToggleDocs,
  className,
}: {
  spaceId: string;
  spaceName: string;
  selectedIds: string[];
  onCite: (c: Citation) => void;
  onToggleDocs: () => void;
  className?: string;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [pending, setPending] = useState<{ content: string; citations: Citation[] } | null>(null);
  const [draft, setDraft] = useState("");
  // A conversation is created on the first question, not on mount — opening a Space
  // should not leave an empty thread behind.
  const conversation = useRef<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pending]);

  async function send(question: string) {
    const q = question.trim();
    if (!q || pending) return;
    setDraft("");
    setMessages((m) => [...m, { id: `u_${Date.now()}`, role: "user", content: q, citations: [] }]);
    setPending({ content: "", citations: [] });

    let content = "";
    let failure = "";
    const citations: Citation[] = [];
    try {
      conversation.current ??= (await createConversation(spaceId)).id;
    } catch (e) {
      setMessages((m) => [...m, { id: `a_${Date.now()}`, role: "assistant",
        content: `Could not start a conversation: ${e instanceof Error ? e.message : e}`,
        citations: [], abstained: true }]);
      setPending(null);
      return;
    }
    for await (const event of streamAnswer(conversation.current, q, selectedIds)) {
      if (event.type === "token") content += event.text;
      else if (event.type === "citation") citations.push(event.citation);
      else if (event.type === "error") failure = event.message;
      else continue;
      setPending({ content, citations: [...citations] });
    }

    setMessages((m) => [
      ...m,
      {
        id: `a_${Date.now()}`,
        role: "assistant",
        content,
        citations,
        abstained: citations.length === 0,
        // A failure part-way through leaves real text on screen. Keep it, and say
        // separately that it was cut short, rather than splicing an error into the answer.
        error: failure || undefined,
      },
    ]);
    setPending(null);
  }

  const idle = messages.length === 0 && !pending;

  return (
    <section className={cn("flex min-w-0 flex-col", className)}>
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-4">
        <Button variant="ghost" onClick={onToggleDocs} className="px-2 py-1 lg:hidden" aria-label="Toggle documents">
          <PanelLeft className="size-4" />
        </Button>
        <h1 className="truncate font-display text-[15px] tracking-tight">{spaceName}</h1>
        <span className="ml-auto text-[11px] text-faint">
          {selectedIds.length} {selectedIds.length === 1 ? "document" : "documents"} in scope
        </span>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-2xl px-5 py-6">
          {idle && (
            <div className="pt-10">
              <h2 className="font-display text-xl tracking-tight">Ask this Space</h2>
              <p className="mt-1.5 text-sm leading-relaxed text-muted">
                Answers come only from the documents in scope. Every claim carries a citation you can open —
                and if the answer is not in your documents, Verbatim says so.
              </p>
              <ul className="mt-5 space-y-2">
                {SUGGESTIONS.map((s) => (
                  <li key={s}>
                    <button
                      type="button"
                      onClick={() => send(s)}
                      className="flex w-full items-center gap-2.5 rounded-lg border border-line bg-surface px-3 py-2.5 text-left text-[13px] transition-colors hover:border-faint hover:bg-sunken"
                    >
                      <Sparkles className="size-3.5 shrink-0 text-faint" />
                      {s}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="space-y-7">
            {messages.map((m) =>
              m.role === "user" ? (
                <div key={m.id} className="flex justify-end">
                  <p className="max-w-[85%] rounded-2xl rounded-br-md bg-sunken px-3.5 py-2 text-[15px] leading-relaxed">
                    {m.content}
                  </p>
                </div>
              ) : (
                <div key={m.id}>
                  <AnswerText content={m.content} citations={m.citations} onCite={onCite} />
                  {m.error ? (
                    <p className="mt-2.5 flex items-start gap-1.5 text-[12px] text-bad">
                      <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
                      {m.content ? `Answer cut short — ${m.error}` : m.error}
                    </p>
                  ) : (
                    <p className="mt-2.5 text-[11px] text-faint">
                      {m.abstained
                        ? "No grounded answer found — nothing cited."
                        : `${m.citations.length} verified ${m.citations.length === 1 ? "citation" : "citations"}`}
                    </p>
                  )}
                </div>
              ),
            )}

            {pending && (
              <div>
                {pending.content ? (
                  <AnswerText content={pending.content} citations={pending.citations} onCite={onCite} />
                ) : (
                  <p className="flex items-center gap-2 text-[13px] text-faint">
                    <span className="size-1.5 animate-pulse rounded-full bg-current" />
                    Searching {selectedIds.length} documents…
                  </p>
                )}
              </div>
            )}
          </div>
          <div ref={bottom} />
        </div>
      </div>

      <div className="shrink-0 px-5 pb-5">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            send(draft);
          }}
          className="mx-auto flex max-w-2xl items-end gap-2 rounded-xl border border-line bg-surface p-2 focus-within:border-faint"
        >
          <textarea
            rows={1}
            value={draft}
            placeholder="Ask a question about these documents…"
            onChange={(e) => setDraft(e.target.value)}
            onInput={(e) => {
              const el = e.currentTarget;
              el.style.height = "auto";
              el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(draft);
              }
            }}
            className="max-h-40 flex-1 resize-none bg-transparent px-2 py-1.5 text-[15px] leading-relaxed outline-none placeholder:text-faint"
          />
          <Button
            type="submit"
            variant="primary"
            disabled={!draft.trim() || !!pending}
            aria-label="Send question"
            className="size-8 shrink-0 justify-center rounded-lg p-0"
          >
            <ArrowUp className="size-4" />
          </Button>
        </form>
        <p className="mx-auto mt-2 max-w-2xl text-center text-[11px] text-faint">
          Grounded in your documents. Quotes are verified against the source before you see them.
        </p>
      </div>
    </section>
  );
}
