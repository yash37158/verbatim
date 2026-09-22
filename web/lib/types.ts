// Mirrors the API contract in ../../PRD.md §7.5. Keep the two in sync.

export type DocStatus = "queued" | "parsing" | "embedding" | "ready" | "failed";

export interface Space {
  id: string;
  name: string;
  description?: string;
  document_count: number;
  last_activity_at: string | null;
}

export interface Doc {
  id: string;
  space_id: string;
  filename: string;
  size_bytes: number;
  page_count: number | null;
  status: DocStatus;
  error?: string;
  retry_after?: string | null; // ISO; when the worker will try this document again
  indexed_chunks?: number | null; // sections already embedded — progress survives retries
}

export interface Citation {
  id: string; // "1", "2" — the label shown in the chip
  chunk_id: string;
  document_id: string;
  document_name: string;
  page: number | null;
  quote: string;
  context: string; // surrounding chunk text, for the source drawer
  sentence_index: number;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  abstained?: boolean;
  error?: string;
}

export type StreamEvent =
  | { type: "token"; text: string }
  | { type: "citation"; citation: Citation }
  | { type: "done"; message_id: string; latency_ms: number }
  | { type: "error"; message: string };
