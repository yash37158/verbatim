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
  total_chunks?: number | null;   // searchable by keyword as soon as this is > 0
  indexed_chunks?: number | null; // of those, how many also have a vector for semantic search
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
  searches?: { query: string; hits: number | null }[]; // what the agent looked up
  abstained?: boolean;
  error?: string;
}

export interface Conversation {
  id: string;
  title: string | null; // null until the first question auto-titles it
  created_at: string;
  last_message_at: string;
  message_count: number;
}

export type StreamEvent =
  | { type: "search"; query: string; hits: number | null } // hits null while it runs
  | { type: "token"; text: string }
  | { type: "citation"; citation: Citation }
  | { type: "done"; message_id: string; latency_ms: number }
  | { type: "error"; message: string };
