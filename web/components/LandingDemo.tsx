"use client";

import { useState } from "react";
import { FileText, X } from "lucide-react";
import { cn } from "@/components/ui";

/**
 * The landing page's centre: the product's actual mechanic, running.
 *
 * Describing "answers with citations" persuades nobody — every tool claims it. Clicking a
 * chip and landing on the exact sentence, in its surrounding paragraph, is the argument.
 * Static data on purpose: no backend, no key, nothing to rate limit.
 */

type Source = {
  id: string;
  document: string;
  page: number;
  quote: string;
  context: string;
};

const SOURCES: Record<string, Source> = {
  "1": {
    id: "1",
    document: "MSA-Northwind-2025.pdf",
    page: 14,
    quote:
      "Either party may terminate this Agreement for convenience upon thirty (30) days’ prior written notice to the other party.",
    context:
      "12.1 Term. This Agreement commences on the Effective Date and continues for an initial term of twenty-four (24) months.\n\n12.2 Termination for Convenience. Either party may terminate this Agreement for convenience upon thirty (30) days’ prior written notice to the other party. No termination fee shall be payable in respect of a termination under this Section 12.2.",
  },
  "2": {
    id: "2",
    document: "DPA-Northwind.pdf",
    page: 7,
    quote:
      "Processor shall, within thirty (30) days, return or securely destroy all Customer Data and certify such destruction in writing",
    context:
      "9. Deletion on Termination. Upon expiry or termination of the Principal Agreement, Processor shall, within thirty (30) days, return or securely destroy all Customer Data and certify such destruction in writing, save to the extent retention is required by applicable law.",
  },
};

const ANSWER = [
  { text: "Either party may end the agreement for convenience with thirty days’ written notice.", cite: "1" },
  { text: " Afterwards, all Customer Data must be returned or destroyed within thirty days, with the deletion certified in writing.", cite: "2" },
];

export function LandingDemo() {
  const [open, setOpen] = useState<string | null>(null);
  const source = open ? SOURCES[open] : null;
  const parts = source ? source.context.split(source.quote) : [];

  return (
    <div className="overflow-hidden rounded-2xl border border-line bg-surface shadow-sm">
      <div className="flex items-center gap-2 border-b border-line px-4 py-2.5 text-[11px] text-faint">
        <FileText className="size-3.5" />
        Vendor Contracts · 2 documents in scope
      </div>

      <div className="grid md:grid-cols-[1fr_auto]">
        <div className="p-5 sm:p-7">
          <p className="mb-5 inline-block rounded-2xl rounded-br-md bg-sunken px-3.5 py-2 text-[15px]">
            If we terminate early, what happens to our data?
          </p>

          <p className="text-[15px] leading-[1.8]">
            {ANSWER.map((part) => (
              <span key={part.cite}>
                {part.text}
                <button
                  type="button"
                  onClick={() => setOpen(open === part.cite ? null : part.cite)}
                  aria-label={`Source ${part.cite}: ${SOURCES[part.cite].document}, page ${SOURCES[part.cite].page}`}
                  aria-pressed={open === part.cite}
                  className={cn(
                    "mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] translate-y-[-1px] items-center justify-center rounded border px-1 align-middle text-[11px] font-semibold transition-colors",
                    open === part.cite
                      ? "border-cite-fg bg-cite-fg text-canvas"
                      : "border-cite-line bg-cite-bg text-cite-fg hover:border-cite-fg",
                  )}
                >
                  {part.cite}
                </button>
              </span>
            ))}
          </p>

          <p className="mt-4 text-[11px] text-faint">
            2 verified citations ·{" "}
            <span className="text-cite-fg">
              {open ? "that quote was checked against the file" : "click a number"}
            </span>
          </p>
        </div>

        <div
          className={cn(
            "border-line bg-sunken transition-all md:w-[20rem] md:border-l",
            source ? "border-t md:border-t-0" : "hidden",
          )}
        >
          {source && (
            <div className="p-4">
              <div className="mb-3 flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-cite-fg">
                    Source {source.id}
                  </p>
                  <p className="truncate text-[13px] font-medium">{source.document}</p>
                  <p className="text-[11px] text-faint">Page {source.page}</p>
                </div>
                <button
                  type="button"
                  onClick={() => setOpen(null)}
                  aria-label="Close source"
                  className="rounded p-1 text-muted transition-colors hover:bg-line hover:text-ink"
                >
                  <X className="size-3.5" />
                </button>
              </div>
              <p className="whitespace-pre-wrap font-display text-[13px] leading-[1.7] text-muted">
                {parts.map((part, i) => (
                  <span key={i}>
                    {part}
                    {i < parts.length - 1 && (
                      <mark className="rounded bg-cite-bg px-0.5 text-ink shadow-[inset_0_-2px_0_var(--cite-fg)]">
                        {source.quote}
                      </mark>
                    )}
                  </span>
                ))}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
