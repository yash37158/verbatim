import { expect, test } from "vitest";
import { GROUNDED } from "./mock";

// PRD FR-17: a citation whose quote is not verbatim in its source is dropped by the
// backend before it ever reaches the client. Fixtures have to honour the same rule,
// or the UI demos a state the real product cannot produce.
test("every mock citation quotes its source verbatim", () => {
  for (const c of GROUNDED.citations) {
    expect(c.context, `citation ${c.id} (${c.document_name})`).toContain(c.quote);
  }
});

test("citations point at sentences the answer actually has", () => {
  const sentences = [...new Intl.Segmenter("en", { granularity: "sentence" }).segment(GROUNDED.text)];
  for (const c of GROUNDED.citations) {
    expect(c.sentence_index).toBeLessThan(sentences.length);
  }
});
