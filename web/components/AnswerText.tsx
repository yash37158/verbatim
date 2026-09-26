"use client";

import { Fragment, type ReactNode } from "react";
import type { Citation } from "@/lib/types";

/**
 * Renders an answer with its citation chips in the right places.
 *
 * Chips are positioned by *sentence terminator count*, matching exactly how the server
 * numbers them (`[.!?]` followed by whitespace or end). Earlier this used Intl.Segmenter,
 * which mostly agreed with the server but not always — and a disagreement silently moves
 * a chip onto the wrong claim, which is precisely the thing this product must not do.
 * Counting the same way the server counts removes the possibility.
 */

const BULLET = /^\s*(?:[-*•]|\d+[.)])\s+/;
const HEADING = /^\s*#{1,6}\s+/;
const ENDS_SENTENCE = /[.!?]\s*$/;

/** Split on the server's sentence boundary, keeping the terminator with its sentence. */
function sentences(text: string): string[] {
  const out: string[] = [];
  let start = 0;
  for (const match of text.matchAll(/[.!?](?=\s|$)/g)) {
    out.push(text.slice(start, match.index! + 1));
    start = match.index! + 1;
  }
  if (start < text.length) out.push(text.slice(start));
  return out.filter((s) => s.length > 0);
}

/** Bold, italic and code. The model uses little else in an answer. */
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|(?<!\*)\*[^*\n]+\*(?!\*)|`[^`]+`)/g).map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4)
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2)
      return (
        <code key={i} className="rounded bg-sunken px-1 py-0.5 font-mono text-[0.85em]">
          {part.slice(1, -1)}
        </code>
      );
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2)
      return <em key={i}>{part.slice(1, -1)}</em>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}

type Piece = { text: string; cites: Citation[] };
type Block = { kind: "p" | "li" | "h"; pieces: Piece[] };

function layout(content: string, citations: Citation[]): Block[] {
  const byIndex = new Map<number, Citation[]>();
  for (const c of citations) {
    byIndex.set(c.sentence_index, [...(byIndex.get(c.sentence_index) ?? []), c]);
  }

  const blocks: Block[] = [];
  let finished = 0; // completed sentences so far, across the whole answer

  for (const raw of content.split("\n")) {
    const line = raw.trim();
    if (!line) continue;

    const bullet = BULLET.exec(line);
    const heading = !bullet && HEADING.exec(line);
    const body = bullet ? line.slice(bullet[0].length) : heading ? line.slice(heading[0].length) : line;
    if (!body) continue;

    const pieces = sentences(body).map((text) => {
      // Only a completed sentence consumes an index — the server counts terminators, and
      // a trailing fragment has not produced one yet.
      const cites = ENDS_SENTENCE.test(text) ? (byIndex.get(finished++) ?? []) : [];
      return { text, cites };
    });
    blocks.push({ kind: bullet ? "li" : heading ? "h" : "p", pieces });
  }
  return blocks;
}

function Chip({ citation, onCite }: { citation: Citation; onCite: (c: Citation) => void }) {
  return (
    <button
      type="button"
      onClick={() => onCite(citation)}
      aria-label={`Source ${citation.id}: ${citation.document_name}${citation.page ? `, page ${citation.page}` : ""}`}
      className="mx-0.5 inline-flex h-[1.15rem] min-w-[1.15rem] translate-y-[-1px] items-center justify-center rounded border border-cite-line bg-cite-bg px-1 align-middle text-[11px] font-semibold text-cite-fg transition-colors hover:border-cite-fg"
    >
      {citation.id}
    </button>
  );
}

function Pieces({ pieces, onCite }: { pieces: Piece[]; onCite: (c: Citation) => void }) {
  return (
    <>
      {pieces.map((piece, i) => (
        <Fragment key={i}>
          {inline(piece.text)}
          {piece.cites.map((c) => (
            <Chip key={c.id} citation={c} onCite={onCite} />
          ))}
        </Fragment>
      ))}
    </>
  );
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
  const blocks = layout(content, citations);

  // Consecutive list items become one <ul>, so they get a single set of bullets rather
  // than one stray marker each.
  const grouped: (Block | { kind: "ul"; items: Block[] })[] = [];
  for (const block of blocks) {
    const last = grouped[grouped.length - 1];
    if (block.kind === "li" && last && "kind" in last && last.kind === "ul") {
      last.items.push(block);
    } else if (block.kind === "li") {
      grouped.push({ kind: "ul", items: [block] });
    } else {
      grouped.push(block);
    }
  }

  return (
    <div className="space-y-3 text-[15px] leading-[1.75]">
      {grouped.map((block, i) =>
        block.kind === "ul" ? (
          <ul key={i} className="ml-1 space-y-1.5">
            {block.items.map((item, j) => (
              <li key={j} className="flex gap-2">
                <span aria-hidden className="mt-[0.55em] size-1 shrink-0 rounded-full bg-faint" />
                <span>
                  <Pieces pieces={item.pieces} onCite={onCite} />
                </span>
              </li>
            ))}
          </ul>
        ) : block.kind === "h" ? (
          <p key={i} className="font-display text-[15px] font-semibold tracking-tight">
            <Pieces pieces={block.pieces} onCite={onCite} />
          </p>
        ) : (
          <p key={i}>
            <Pieces pieces={block.pieces} onCite={onCite} />
          </p>
        ),
      )}
    </div>
  );
}
