"use client";

import { Fragment, type ReactNode } from "react";
import type { Citation } from "@/lib/types";

// Intl.Segmenter handles "Inc." and "v." far better than a regex would, and it ships
// with the platform. Partial text mid-stream segments fine: completed sentences keep
// their index, so a citation that arrives late still lands on the right sentence.
const segmenter = new Intl.Segmenter("en", { granularity: "sentence" });

// ponytail: answers are prose. If the model starts emitting tables, swap in react-markdown.
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`"))
      return (
        <code key={i} className="rounded bg-sunken px-1 py-0.5 font-mono text-[0.85em]">
          {part.slice(1, -1)}
        </code>
      );
    return <Fragment key={i}>{part}</Fragment>;
  });
}

export function AnswerText({
  content,
  citations,
  onCite,
}: {
  content: string;
  citations: Citation[];
  onCite: (c: Citation) => void;
}) {
  const sentences = [...segmenter.segment(content)].map((s) => s.segment);

  return (
    <p className="text-[15px] leading-[1.75]">
      {sentences.map((sentence, i) => (
        <Fragment key={i}>
          {inline(sentence)}
          {citations
            .filter((c) => c.sentence_index === i)
            .map((c) => (
              <button
                key={c.id}
                type="button"
                onClick={() => onCite(c)}
                aria-label={`Source ${c.id}: ${c.document_name}${c.page ? `, page ${c.page}` : ""}`}
                className="mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] translate-y-[-1px] items-center justify-center rounded border border-cite-line bg-cite-bg px-1 align-middle text-[11px] font-semibold text-cite-fg transition-colors hover:border-cite-fg"
              >
                {c.id}
              </button>
            ))}
        </Fragment>
      ))}
    </p>
  );
}
