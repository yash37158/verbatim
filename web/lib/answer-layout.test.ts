import { expect, test } from "vitest";

/**
 * The placement rule, isolated from React: a chip with sentence_index N attaches after
 * the (N+1)th completed sentence, counting the way the server counts — `[.!?]` followed
 * by whitespace or end, across the whole answer including bullets.
 *
 * This is worth pinning because a disagreement here is invisible: the chip still renders,
 * just on the wrong claim, which is the one failure this product cannot afford.
 */
function placements(content: string, indices: number[]): string[] {
  const BULLET = /^\s*(?:[-*•]|\d+[.)])\s+/;
  const HEADING = /^\s*#{1,6}\s+/;
  const ENDS = /[.!?]\s*$/;
  const out: string[] = [];
  let finished = 0;

  for (const raw of content.split("\n")) {
    const line = raw.trim();
    if (!line) continue;
    const bullet = BULLET.exec(line);
    const heading = !bullet && HEADING.exec(line);
    const body = bullet ? line.slice(bullet[0].length) : heading ? line.slice(heading[0].length) : line;
    if (!body) continue;

    const parts: string[] = [];
    let start = 0;
    for (const m of body.matchAll(/[.!?](?=\s|$)/g)) {
      parts.push(body.slice(start, m.index! + 1));
      start = m.index! + 1;
    }
    if (start < body.length) parts.push(body.slice(start));

    for (const part of parts.filter((p) => p.length)) {
      if (!ENDS.test(part)) continue;
      const here = finished++;
      if (indices.includes(here)) out.push(part.trim());
    }
  }
  return out;
}

/** How the server numbers a citation: terminators emitted so far, minus one. */
function serverIndex(emittedSoFar: string): number {
  return Math.max(0, (emittedSoFar.match(/[.!?](?=\s|$)/g) ?? []).length - 1);
}

test("a chip lands on the sentence the server numbered", () => {
  const answer = "Notice is thirty days. Data is deleted in sixty.";
  const i = serverIndex("Notice is thirty days.");
  expect(placements(answer, [i])).toEqual(["Notice is thirty days."]);
});

test("bullets do not shift the count", () => {
  const answer = [
    "The assessment identifies several risks:",
    "* Infrastructure is a single host. ",
    "* Software is superseded.",
  ].join("\n");
  // The server saw "…single host." as the first completed sentence — the heading line has
  // no terminator, so it never consumed an index.
  const first = serverIndex("The assessment identifies several risks:\n* Infrastructure is a single host.");
  expect(placements(answer, [first])).toEqual(["Infrastructure is a single host."]);

  const second = serverIndex(
    "The assessment identifies several risks:\n* Infrastructure is a single host. \n* Software is superseded.",
  );
  expect(placements(answer, [second])).toEqual(["Software is superseded."]);
});

test("a heading line with no terminator consumes no index", () => {
  const answer = "### Risks\nThe host is single. It has no failover.";
  expect(placements(answer, [0])).toEqual(["The host is single."]);
  expect(placements(answer, [1])).toEqual(["It has no failover."]);
});

test("an abbreviation does not split the sentence, because the server's rule does not", () => {
  // "Inc." is followed by a space, so BOTH sides count it. Agreement is what matters here,
  // not linguistic correctness — the chip lands where the server said.
  const answer = "Northwind Inc. is the processor. It deletes data.";
  const i = serverIndex("Northwind Inc.");
  expect(placements(answer, [i])).toEqual(["Northwind Inc."]);
});

test("a trailing fragment holds no chip", () => {
  const answer = "One complete sentence. Then an unfinished";
  expect(placements(answer, [0])).toEqual(["One complete sentence."]);
  expect(placements(answer, [1])).toEqual([]);
});

test("several chips can share a sentence", () => {
  const answer = "Both clauses say thirty days.";
  expect(placements(answer, [0, 0])).toEqual(["Both clauses say thirty days."]);
});
