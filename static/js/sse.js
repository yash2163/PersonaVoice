// fetch-stream SSE parser. EventSource can't POST, so we read the body ourselves.

export class HttpError extends Error {
  constructor(status, body) {
    super(body?.message || `HTTP ${status}`);
    this.status = status;
    this.body = body;
  }
}

/**
 * POST JSON and dispatch SSE events to handlers[eventName](data).
 * Resolves with { sawDone, sawError }. A stream that ends without `done` or `error`
 * is the caller's cue to show a retry (sawDone === false).
 */
export async function streamSSE(url, { body, signal, handlers }) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!resp.ok) {
    let parsed = null;
    try { parsed = await resp.json(); } catch { /* not json */ }
    throw new HttpError(resp.status, parsed);
  }

  const result = { sawDone: false, sawError: false };
  const decoder = new TextDecoder();
  const reader = resp.body.getReader();
  let buf = "";

  const dispatch = (block) => {
    let event = "message";
    const dataLines = [];
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
    }
    if (!dataLines.length) return;
    let data;
    try { data = JSON.parse(dataLines.join("\n")); } catch { return; }
    if (event === "done") result.sawDone = true;
    if (event === "error") result.sawError = true;
    handlers[event]?.(data);
  };

  const drain = (final) => {
    buf = buf.replace(/\r\n/g, "\n");
    let idx;
    while ((idx = buf.indexOf("\n\n")) !== -1) {
      dispatch(buf.slice(0, idx));
      buf = buf.slice(idx + 2);
    }
    if (final && buf.trim()) { dispatch(buf); buf = ""; }
  };

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    drain(false);
  }
  buf += decoder.decode();
  drain(true);
  return result;
}
