// Run: node --test tests/js
import test from "node:test";
import assert from "node:assert/strict";
import { streamSSE, HttpError } from "../../static/js/sse.js";

function mockFetch(chunks, { status = 200, json = null } = {}) {
  const enc = new TextEncoder();
  globalThis.fetch = async () => ({
    ok: status === 200,
    status,
    json: async () => json,
    body: new ReadableStream({
      start(c) { for (const ch of chunks) c.enqueue(enc.encode(ch)); c.close(); },
    }),
  });
}

const run = async (chunks) => {
  mockFetch(chunks);
  const seen = [];
  const handlers = new Proxy({}, { get: (_, name) => (d) => seen.push([name, d]) });
  const res = await streamSSE("/x", { body: {}, handlers });
  return { res, seen };
};

test("events split across arbitrary chunk boundaries", async () => {
  const full = 'event: meta\ndata: {"request_id":"r1"}\n\nevent: text\ndata: {"delta":"héllo"}\n\nevent: done\ndata: {"timings":{}}\n\n';
  const chunks = [...full].map((c) => c); // one character per chunk
  const { res, seen } = await run(chunks);
  assert.deepEqual(seen.map((s) => s[0]), ["meta", "text", "done"]);
  assert.equal(seen[1][1].delta, "héllo");
  assert.equal(res.sawDone, true);
});

test("CRLF separators and a final event without trailing blank line", async () => {
  const { res, seen } = await run(['event: text\r\ndata: {"delta":"a"}\r\n\r\nevent: done\r\ndata: {}']);
  assert.deepEqual(seen.map((s) => s[0]), ["text", "done"]);
  assert.equal(res.sawDone, true);
});

test("stream ending without done is reported", async () => {
  const { res } = await run(['event: text\ndata: {"delta":"a"}\n\n']);
  assert.equal(res.sawDone, false);
  assert.equal(res.sawError, false);
});

test("error event is flagged", async () => {
  const { res } = await run(['event: error\ndata: {"code":"x","message":"m","spoken":"s"}\n\n']);
  assert.equal(res.sawError, true);
});

test("non-200 throws HttpError with parsed body", async () => {
  mockFetch([], { status: 429, json: { message: "slow down" } });
  await assert.rejects(streamSSE("/x", { body: {}, handlers: {} }), (e) => e instanceof HttpError && e.status === 429 && e.message === "slow down");
});
