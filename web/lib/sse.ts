/**
 * Pull whole SSE frames out of a growing buffer.
 *
 * The network splits wherever it likes, so a frame can arrive in pieces and two
 * frames can arrive in one chunk. Callers keep `rest` and prepend the next chunk.
 */
export function drainSSE(buffer: string): { events: unknown[]; rest: string } {
  const events: unknown[] = [];
  let rest = buffer.replace(/\r\n/g, "\n"); // SSE permits CRLF; normalise once
  let i: number;

  while ((i = rest.indexOf("\n\n")) !== -1) {
    const frame = rest.slice(0, i);
    rest = rest.slice(i + 2);
    const data = frame
      .split("\n")
      .filter((line) => line.startsWith("data:")) // drops `:` comments and `event:` lines
      .map((line) => line.slice(5).replace(/^ /, "")) // exactly one optional leading space
      .join("\n");

    if (!data || data === "[DONE]") continue;
    try {
      events.push(JSON.parse(data));
    } catch {
      // A truncated or malformed frame is the server's bug, not a reason to kill the stream.
    }
  }
  return { events, rest };
}
