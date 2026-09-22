"use client";

import { ExternalLink, X } from "lucide-react";
import type { Citation } from "@/lib/types";
import { Button } from "@/components/ui";

export function SourceDrawer({ citation, onClose }: { citation: Citation; onClose: () => void }) {
  // The quote is verified server-side before it reaches us, so it should always be
  // present. If it somehow is not, show the passage plain rather than nothing.
  const parts = citation.context.split(citation.quote);

  return (
    <aside
      className="fixed inset-0 z-40 flex flex-col border-line bg-surface sm:left-auto sm:w-[27rem] sm:border-l lg:static lg:inset-auto lg:z-auto lg:w-[24rem] lg:shrink-0"
      aria-label="Source passage"
    >
      <div className="flex items-start justify-between gap-3 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-cite-fg">
            Source {citation.id}
          </p>
          <p className="truncate text-sm font-medium" title={citation.document_name}>
            {citation.document_name}
          </p>
          <p className="text-xs text-faint">
            {citation.page ? `Page ${citation.page}` : "Unpaginated"} · chunk {citation.chunk_id}
          </p>
        </div>
        <Button variant="ghost" onClick={onClose} aria-label="Close source panel" className="px-2 py-1">
          <X className="size-4" />
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        <p className="mb-3 text-[11px] font-semibold uppercase tracking-wide text-faint">
          Quoted passage in context
        </p>
        <div className="whitespace-pre-wrap font-display text-[15px] leading-[1.8] text-muted">
          {parts.length > 1
            ? parts.map((part, i) => (
                <span key={i}>
                  {part}
                  {i < parts.length - 1 && (
                    <mark className="rounded bg-cite-bg px-1 py-0.5 text-ink shadow-[inset_0_-2px_0_var(--cite-fg)]">
                      {citation.quote}
                    </mark>
                  )}
                </span>
              ))
            : citation.context}
        </div>
      </div>

      <div className="border-t border-line px-4 py-3">
        <Button className="w-full justify-center">
          <ExternalLink className="size-4" />
          Open in document
        </Button>
      </div>
    </aside>
  );
}
