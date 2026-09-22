// In-memory stand-in for the FastAPI backend, so the UI is reviewable before M1.
// Deleted wholesale once NEXT_PUBLIC_API_URL is set — nothing else imports it.
import type { Citation, Doc, Space, StreamEvent } from "./types";

const MSA = "d_msa";
const DPA = "d_dpa";

export const spaces: Space[] = [
  {
    id: "s_contracts",
    name: "Vendor Contracts",
    description: "Northwind master agreement, DPA, and active statements of work.",
    document_count: 4,
    last_activity_at: "2026-09-21T16:40:00Z",
  },
  {
    id: "s_protocols",
    name: "Clinical Protocols",
    description: "Site protocols and amendments for the RX-114 trial.",
    document_count: 2,
    last_activity_at: "2026-09-18T09:12:00Z",
  },
  {
    id: "s_handbook",
    name: "2026 Policy Handbook",
    description: "Company handbook, expense policy, leave policy.",
    document_count: 1,
    last_activity_at: null,
  },
];

export const docs: Record<string, Doc[]> = {
  s_contracts: [
    { id: MSA, space_id: "s_contracts", filename: "MSA-Northwind-2025.pdf", size_bytes: 2_410_000, page_count: 48, status: "ready" },
    { id: DPA, space_id: "s_contracts", filename: "DPA-Northwind.pdf", size_bytes: 640_000, page_count: 12, status: "ready" },
    { id: "d_sow", space_id: "s_contracts", filename: "SOW-Q3-Migration.docx", size_bytes: 180_000, page_count: 9, status: "embedding" },
    {
      id: "d_amend",
      space_id: "s_contracts",
      filename: "Amendment-2-scanned.pdf",
      size_bytes: 8_900_000,
      page_count: 3,
      status: "failed",
      error: "No extractable text layer — this looks like a scan. OCR is not available yet.",
    },
  ],
  s_protocols: [
    { id: "d_p1", space_id: "s_protocols", filename: "RX-114-Protocol-v4.pdf", size_bytes: 5_200_000, page_count: 132, status: "ready" },
    { id: "d_p2", space_id: "s_protocols", filename: "Site-Amendment-07.pdf", size_bytes: 310_000, page_count: 6, status: "ready" },
  ],
  s_handbook: [
    { id: "d_h1", space_id: "s_handbook", filename: "Handbook-2026.pdf", size_bytes: 1_100_000, page_count: 64, status: "ready" },
  ],
};

export const GROUNDED: { text: string; citations: Citation[] } = {
  text:
    "Northwind may terminate the agreement for convenience with 30 days' written notice to the other party. " +
    "Termination for cause is immediate once a material breach has gone uncured for 15 days after notice. " +
    "On any termination, Northwind must return or destroy all Customer Data within 30 days and certify the deletion in writing.",
  citations: [
    {
      id: "1",
      chunk_id: "ch_0412",
      document_id: MSA,
      document_name: "MSA-Northwind-2025.pdf",
      page: 14,
      sentence_index: 0,
      quote: "Either party may terminate this Agreement for convenience upon thirty (30) days' prior written notice to the other party.",
      context:
        "12.1 Term. This Agreement commences on the Effective Date and continues for an initial term of twenty-four (24) months.\n\n" +
        "12.2 Termination for Convenience. Either party may terminate this Agreement for convenience upon thirty (30) days' prior written notice to the other party. " +
        "No termination fee shall be payable in respect of a termination under this Section 12.2.",
    },
    {
      id: "2",
      chunk_id: "ch_0413",
      document_id: MSA,
      document_name: "MSA-Northwind-2025.pdf",
      page: 14,
      sentence_index: 1,
      quote: "a material breach which remains uncured fifteen (15) days after written notice thereof",
      context:
        "12.3 Termination for Cause. Either party may terminate this Agreement with immediate effect upon a material breach which remains uncured fifteen (15) days after written notice thereof, " +
        "or immediately upon the other party's insolvency, liquidation, or appointment of a receiver.",
    },
    {
      id: "3",
      chunk_id: "ch_0088",
      document_id: DPA,
      document_name: "DPA-Northwind.pdf",
      page: 7,
      sentence_index: 2,
      quote: "Processor shall, within thirty (30) days, return or securely destroy all Customer Data and certify such destruction in writing",
      context:
        "9. Deletion on Termination. Upon expiry or termination of the Principal Agreement, Processor shall, within thirty (30) days, return or securely destroy all Customer Data and certify such destruction in writing, " +
        "save to the extent retention is required by applicable law.",
    },
  ],
};

export const ABSTAINED: { text: string; citations: Citation[] } = {
  text:
    "I couldn't find this in your documents. The closest passages cover the termination fee position and the invoicing cadence, " +
    "but neither states a price. If pricing lives in a separate order form, add it to this Space and ask again.",
  citations: [],
};

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/**
 * Mock create and upload mutate the same stores `listSpaces` and `listDocuments` read, and
 * the upload advances its own status on a timer. The UI then polls identically in both
 * modes instead of carrying a branch for "are we pretending".
 */
export function createSpace(name: string, description?: string): Space {
  const space: Space = {
    id: `s_${Math.random().toString(36).slice(2, 10)}`,
    name,
    description,
    document_count: 0,
    last_activity_at: null,
  };
  spaces.unshift(space);
  docs[space.id] = [];
  return space;
}

export function retryDocument(documentId: string): Doc {
  const doc = Object.values(docs).flat().find((d) => d.id === documentId);
  if (!doc) throw new Error("Document not found");
  doc.status = "queued";
  doc.error = undefined;
  setTimeout(() => (doc.status = "ready"), 2500);
  return doc;
}

export function uploadDocument(spaceId: string, file: File): Doc {
  const doc: Doc = {
    id: `d_${Math.random().toString(36).slice(2, 10)}`,
    space_id: spaceId,
    filename: file.name,
    size_bytes: file.size,
    page_count: null,
    status: "queued",
  };
  (docs[spaceId] ??= []).push(doc);
  const advance = (status: Doc["status"], after: number) =>
    setTimeout(() => {
      doc.status = status;
      if (status === "ready") doc.page_count = Math.max(1, Math.round(file.size / 40_000));
    }, after);
  advance("parsing", 800);
  advance("embedding", 2000);
  advance("ready", 3600);
  return doc;
}

export async function* streamMockAnswer(question: string): AsyncGenerator<StreamEvent> {
  const unanswerable = /\b(price|pricing|cost|fee|rate card|how much)\b/i.test(question);
  const answer = unanswerable ? ABSTAINED : GROUNDED;
  const started = Date.now();

  await sleep(420); // retrieval + rerank, as the real pipeline would feel

  let sentence = 0;
  const sent = new Set<string>();
  for (const token of answer.text.match(/\S+\s*/g) ?? []) {
    yield { type: "token", text: token };
    await sleep(16);
    if (!/\.\s*$/.test(token)) continue; // \s* — the last token of the answer has no trailing space
    for (const c of answer.citations.filter((x) => x.sentence_index === sentence)) {
      sent.add(c.id);
      yield { type: "citation", citation: c };
    }
    sentence++;
  }
  // Safety net: a citation whose sentence the tokeniser never closed still belongs to the answer.
  for (const c of answer.citations.filter((x) => !sent.has(x.id))) yield { type: "citation", citation: c };
  yield { type: "done", message_id: `m_${Date.now()}`, latency_ms: Date.now() - started };
}
