"use client";

import { useRef, useState } from "react";
import { AlertCircle, FileText, RotateCcw, Upload } from "lucide-react";
import type { Doc } from "@/lib/types";
import { StatusPill, cn, formatBytes, formatSoon } from "@/components/ui";

export function DocumentsPane({
  documents,
  selected,
  onToggle,
  onUpload,
  onRetry,
  className,
}: {
  documents: Doc[];
  selected: Set<string>;
  onToggle: (id: string) => void;
  onUpload: (files: File[]) => void;
  onRetry: (id: string) => void;
  className?: string;
}) {
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const ready = documents.filter((d) => d.status === "ready").length;

  return (
    <div className={cn("flex flex-col bg-surface", className)}>
      <div className="flex items-baseline justify-between px-4 pb-2 pt-4">
        <h2 className="text-[11px] font-semibold uppercase tracking-wide text-faint">Documents</h2>
        <span className="text-[11px] text-faint">{ready} searchable</span>
      </div>

      <ul className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
        {documents.map((doc) => {
          const usable = doc.status === "ready";
          return (
            <li key={doc.id}>
              <label
                className={cn(
                  "flex cursor-pointer gap-2.5 rounded-lg p-2 transition-colors hover:bg-sunken",
                  !usable && "cursor-default opacity-70 hover:bg-transparent",
                )}
              >
                <input
                  type="checkbox"
                  checked={usable && selected.has(doc.id)}
                  disabled={!usable}
                  onChange={() => onToggle(doc.id)}
                  className="mt-1 size-3.5 shrink-0 accent-[var(--cite-fg)]"
                />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5">
                    {doc.status === "failed" ? (
                      <AlertCircle className="size-3.5 shrink-0 text-bad" />
                    ) : (
                      <FileText className="size-3.5 shrink-0 text-faint" />
                    )}
                    <span className="truncate text-[13px]" title={doc.filename}>
                      {doc.filename}
                    </span>
                  </span>
                  <span className="mt-1 flex items-center gap-2 pl-5 text-[11px] text-faint">
                    <StatusPill status={doc.status} />
                    <span aria-hidden>·</span>
                    <span>{doc.page_count ? `${doc.page_count} pp` : formatBytes(doc.size_bytes)}</span>
                  </span>
                  {doc.error && (
                    <span className="mt-1 block pl-5">
                      {doc.retry_after && doc.status === "queued" && (
                        // "It will retry" with no time reads as "it is stuck".
                        <span className="mb-1 block text-[11px] leading-snug text-warn">
                          {doc.indexed_chunks
                            ? `${doc.indexed_chunks} sections indexed so far · continues `
                            : "Next attempt "}
                          {formatSoon(doc.retry_after)} · nothing for you to do
                        </span>
                      )}
                      {/* Provider errors arrive as a wall of JSON; the server condenses
                          them, and this clamps whatever still gets through. */}
                      <span className="line-clamp-3 text-[11px] leading-snug text-bad" title={doc.error}>
                        {doc.error}
                      </span>
                      {doc.status === "failed" && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.preventDefault();
                            onRetry(doc.id);
                          }}
                          className="mt-1.5 inline-flex items-center gap-1 rounded border border-line px-1.5 py-0.5 text-[11px] font-medium transition-colors hover:bg-sunken"
                        >
                          <RotateCcw className="size-3" />
                          Retry
                        </button>
                      )}
                    </span>
                  )}
                </span>
              </label>
            </li>
          );
        })}
      </ul>

      <div className="p-3">
        <input
          ref={input}
          type="file"
          multiple
          accept=".pdf,.txt,.md,.docx"
          className="sr-only"
          onChange={(e) => {
            onUpload([...(e.target.files ?? [])]);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          onClick={() => input.current?.click()}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDragging(false);
            onUpload([...e.dataTransfer.files]);
          }}
          className={cn(
            "flex w-full flex-col items-center gap-1 rounded-lg border border-dashed px-3 py-4 text-center transition-colors",
            dragging ? "border-cite-fg bg-cite-bg/40" : "border-line hover:border-faint hover:bg-sunken",
          )}
        >
          <Upload className="size-4 text-faint" />
          <span className="text-[13px] font-medium">Add documents</span>
          <span className="text-[11px] text-faint">PDF, DOCX, TXT, MD · 50 MB each</span>
        </button>
      </div>
    </div>
  );
}
