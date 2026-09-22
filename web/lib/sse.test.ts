import { expect, test } from "vitest";
import { drainSSE } from "./sse";

test("splits multiple frames from one chunk", () => {
  const { events, rest } = drainSSE('data: {"a":1}\n\ndata: {"a":2}\n\n');
  expect(events).toEqual([{ a: 1 }, { a: 2 }]);
  expect(rest).toBe("");
});

test("holds a partial frame until the rest arrives", () => {
  const first = drainSSE('data: {"type":"tok');
  expect(first.events).toEqual([]);

  const second = drainSSE(first.rest + 'en","text":"hi"}\n\n');
  expect(second.events).toEqual([{ type: "token", text: "hi" }]);
  expect(second.rest).toBe("");
});

test("joins multi-line data and ignores comments and event lines", () => {
  const { events } = drainSSE(': keepalive\nevent: citation\ndata: {"q":\ndata: "a\\nb"}\n\n');
  expect(events).toEqual([{ q: "a\nb" }]);
});

test("handles CRLF line endings", () => {
  const { events, rest } = drainSSE('data: {"a":1}\r\n\r\n');
  expect(events).toEqual([{ a: 1 }]);
  expect(rest).toBe("");
});

test("skips a malformed frame without dropping the next one", () => {
  const { events } = drainSSE('data: {oops\n\ndata: {"a":1}\n\n');
  expect(events).toEqual([{ a: 1 }]);
});

test("ignores the [DONE] sentinel", () => {
  expect(drainSSE("data: [DONE]\n\n").events).toEqual([]);
});
