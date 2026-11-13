// State machine + UI wiring.
// States: idle | recording | transcribing | thinking | speaking | error
import { AudioQueue } from "./audio-queue.js";
import { FillerController } from "./filler.js";
import { Recorder, recordingSupported } from "./recorder.js";
import { VoiceLine } from "./voice-line.js";
import { HttpError, streamSSE } from "./sse.js";

const $ = (id) => document.getElementById(id);
const el = {
  talk: $("talk"), label: document.querySelector(".talk-label"), status: $("status"), stop: $("stop"), retry: $("retry"),
  q: $("q-caption"), a: $("a-caption"), turnQ: $("turn-q"), turnA: $("turn-a"), empty: $("empty"), chips: $("chips"),
  form: $("text-form"), input: $("text-input"), toast: $("toast"), debug: $("debug"), hint: $("inapp-hint"),
};
const DEBUG = new URLSearchParams(location.search).get("debug") === "1";
// Button label and a short status line for each state.
const LABEL = {
  idle: "Tap to talk",
  recording: "Tap when you're done",
  transcribing: "Cancel",
  thinking: "Ask something else",
  speaking: "Ask something else",
  error: "Try again",
};
const STATUS = {
  idle: "",
  recording: "Listening. Speak naturally.",
  transcribing: "Turning your question into text.",
  thinking: "Yash is thinking.",
  speaking: "Yash is speaking.",
};
const VOICE_MODE = { idle: "idle", error: "idle", recording: "user", transcribing: "thinking", thinking: "thinking", speaking: "bot" };
const MAX_Q = 500, MAX_HISTORY = 6, MAX_ANSWER_CHARS = 1500;

const queue = new AudioQueue();
const recorder = new Recorder(() => queue.ctx);
const voice = new VoiceLine($("voice-line"));
let filler = new FillerController(queue);
let cfg = { sample_questions: [], fillers: [], apology: null, filler_threshold_ms: 800, max_recording_s: 30 };

let state = "idle";
let turn = null; // the live turn, or null
let history = [];
let micOk = recordingSupported();
let starting = false;
let autoStop = null;
let toastTimer = null;

// ---------------------------------------------------------------------- UI helpers
function setState(s, text) {
  state = s;
  el.talk.dataset.state = s;
  el.label.textContent = LABEL[s] ?? LABEL.idle;
  el.status.textContent = text ?? STATUS[s] ?? "";
  el.status.classList.toggle("error", s === "error");
  el.stop.hidden = !(s === "thinking" || s === "speaking");
  voice.setMode(VOICE_MODE[s] ?? "idle", s === "recording" ? recorder.analyser : s === "speaking" ? queue.analyser : null);
}

function toast(msg, ms = 6000) {
  clearTimeout(toastTimer);
  el.toast.textContent = msg;
  el.toast.hidden = false;
  toastTimer = setTimeout(() => { el.toast.hidden = true; }, ms);
}

function showError(msg, { focusText = false } = {}) {
  setState("error", msg);
  toast(msg);
  if (focusText) el.input.focus();
}

function setQuestion(text) { el.q.textContent = `“${text}”`; el.turnQ.hidden = false; el.empty.hidden = true; }
function clearAnswer() { el.a.textContent = ""; el.turnA.hidden = true; }

// ---------------------------------------------------------------------- turns
function interrupt() {
  if (turn) { turn.cancelled = true; turn.abort.abort(); }
  filler.cancelTurn();
  queue.stop();
  turn = null;
  el.retry.hidden = true;
}

function beginTurn() {
  interrupt();
  turn = {
    id: Math.random().toString(36).slice(2),
    abort: new AbortController(),
    t0: performance.now(),
    cancelled: false, streamEnded: false, gotAudio: false, incomplete: false, errored: false,
    answer: "", spoken: [], question: "", sttMs: null, firstAnswerAt: null, timings: null, fillersPlayed: 0, requestId: "",
  };
  clearAnswer();
  filler.startTurn();
  return turn;
}

function maybeFinish() {
  const t = turn;
  if (!t || !t.streamEnded || queue.busy) return;
  turn = null;
  filler.cancelTurn();
  if (state !== "error") setState("idle");
  if (DEBUG) renderDebug(t);
}

queue.onItemStart = (item) => {
  const t = turn;
  if (!t) return;
  voice.setMode("bot", queue.analyser);
  if (item.kind === "filler") { t.fillersPlayed++; return; }
  if (!t.firstAnswerAt) t.firstAnswerAt = performance.now();
  if (state === "thinking" || state === "transcribing") setState("speaking");
};
queue.onDrain = () => {
  const t = turn;
  if (!t) return;
  if (!t.streamEnded && state === "speaking") setState("thinking");
  else if (state === "thinking") voice.setMode("thinking");
  maybeFinish();
};

const b64ToBuffer = (b64) => {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
};

async function runAsk(t, rawQuestion, sttMs) {
  const question = rawQuestion.trim().slice(0, MAX_Q);
  t.question = question;
  t.sttMs = sttMs;
  setQuestion(question);
  setState("thinking");

  const handlers = {
    meta: (d) => { t.requestId = d.request_id; },
    text: (d) => {
      if (t.cancelled) return;
      t.answer += d.delta;
      if (el.turnA.hidden) {
        el.turnA.hidden = false;
        // Keep the answer in view when it starts (on phones it begins below the fold).
        el.turnA.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
      }
      el.a.textContent = t.answer;
    },
    audio: (d) => {
      if (t.cancelled) return;
      t.gotAudio = true;
      t.spoken.push(d.text);
      filler.noteAnswer();
      queue.enqueueBuffer("answer", queue.decode(b64ToBuffer(d.b64)), { text: d.text });
    },
    tts_fallback: (d) => {
      if (t.cancelled) return;
      t.gotAudio = true;
      t.spoken.push(d.text);
      filler.noteAnswer();
      queue.enqueueSpeech(d.text);
    },
    done: (d) => {
      t.timings = d.timings;
      t.fallbackUsed = d.fallback_used;
      if (d.truncated && !t.cancelled && t.spoken.length) {
        // The server stopped a too-long answer at a sentence boundary; show exactly what is spoken.
        t.answer = t.spoken.join(" ");
        el.a.textContent = t.answer;
      }
    },
    error: (d) => {
      if (t.cancelled) return;
      t.errored = true;
      filler.cancelTurn();
      if (d.spoken && !t.gotAudio) {
        if (filler.apology) queue.enqueueBuffer("apology", filler.apology, { text: d.spoken });
        else queue.enqueueSpeech(d.spoken, "apology");
      }
      toast(d.message || "Something went wrong. Please try again.");
      if (d.code === "llm_interrupted") el.retry.hidden = false;
    },
  };

  try {
    const body = { question, history, ...(sttMs != null ? { stt_ms: sttMs } : {}) };
    const res = await streamSSE("/api/ask", { body, signal: t.abort.signal, handlers });
    if (t.cancelled) return;
    if (res.sawDone && !t.errored) {
      history = [...history, { role: "user", content: question }, { role: "assistant", content: t.answer.slice(0, MAX_ANSWER_CHARS) }];
      while (history.length > MAX_HISTORY) history.splice(0, 2);
    } else if (!res.sawDone && !res.sawError) {
      t.incomplete = true; // network dropped mid-stream: play what arrived, offer retry
      el.retry.hidden = false;
      toast("The connection dropped. You can retry.");
    }
  } catch (e) {
    if (t.cancelled || e.name === "AbortError") return;
    filler.cancelTurn();
    if (e instanceof HttpError && e.status === 429) {
      const msg = e.body?.message || "You're asking a little fast. Give it a minute and try again.";
      showError(msg);
      speakShort(msg);
      turn = null;
      return;
    }
    if (!t.gotAudio) {
      showError(e instanceof HttpError ? (e.body?.message || "Something went wrong.") : "Couldn't reach the server. Check your connection and try again.", { focusText: true });
      el.retry.hidden = false;
      turn = null;
      return;
    }
    t.incomplete = true;
    el.retry.hidden = false;
  }
  t.streamEnded = true;
  maybeFinish();
}

function speakShort(text) {
  try { queue.enqueueSpeech(text, "apology"); } catch { /* ignore */ }
}

// ---------------------------------------------------------------------- text input
async function submitText(q) {
  await queue.unlock();
  stopRecordingQuietly();
  const t = beginTurn();
  runAsk(t, q, null);
}

el.form.addEventListener("submit", (e) => {
  e.preventDefault();
  const q = el.input.value.trim();
  if (!q) return;
  el.input.value = "";
  submitText(q);
});

function renderChips() {
  el.chips.textContent = "";
  for (const q of cfg.sample_questions) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.textContent = q;
    b.addEventListener("click", () => submitText(q));
    el.chips.append(b);
  }
}

// ---------------------------------------------------------------------- voice input
function handleMicError(e) {
  micOk = false;
  const name = e?.name || "";
  if (name === "NotAllowedError" || name === "SecurityError") {
    showError("Microphone access is blocked, so type your question instead. (In an in-app browser, open this page in Chrome or Safari.)", { focusText: true });
  } else if (name === "NotFoundError" || name === "OverconstrainedError") {
    showError("No microphone found. You can type your question instead.", { focusText: true });
  } else {
    showError("Couldn't start the microphone. You can type your question instead.", { focusText: true });
  }
  el.hint.hidden = false;
}

async function startRecording() {
  if (!micOk) {
    showError("Voice isn't available here. Type your question instead.", { focusText: true });
    return;
  }
  starting = true;
  // Browsers show a permission prompt on first use and don't resolve until it is answered, so say so.
  el.label.textContent = "Waiting for microphone…";
  el.status.classList.remove("error");
  el.status.textContent = "Choose Allow when your browser asks to use the microphone.";
  try {
    await recorder.start();
  } catch (e) {
    starting = false;
    handleMicError(e);
    return;
  }
  starting = false;
  setState("recording");
  autoStop = setTimeout(stopRecording, (cfg.max_recording_s || 30) * 1000);
}

function stopRecordingQuietly() {
  clearTimeout(autoStop);
  if (recorder.recording) recorder.cancel();
}

async function stopRecording() {
  clearTimeout(autoStop);
  const rec = await recorder.stop();
  if (!rec || rec.durationMs < 400) {
    setState("idle");
    toast("That was too short. Tap, speak, then tap again.", 3500);
    return;
  }
  const t = beginTurn();
  setState("transcribing");
  try {
    const fd = new FormData();
    fd.append("audio", rec.blob, `rec.${rec.ext}`);
    fd.append("duration_ms", String(rec.durationMs));
    const resp = await fetch("/api/transcribe", { method: "POST", body: fd, signal: t.abort.signal });
    if (!resp.ok) {
      let body = null;
      try { body = await resp.json(); } catch { /* ignore */ }
      throw new HttpError(resp.status, body);
    }
    const { text, ms } = await resp.json();
    if (t.cancelled) return;
    runAsk(t, text, ms);
  } catch (e) {
    if (t.cancelled || e.name === "AbortError") return;
    filler.cancelTurn();
    turn = null;
    if (e instanceof HttpError && e.status === 429) {
      showError(e.body?.message || "You're asking a little fast. Give it a minute.");
    } else {
      showError("Couldn't hear that clearly. Try again or type your question.", { focusText: true });
    }
  }
}

async function onTalk() {
  await queue.unlock(); // inside the user gesture: required on iOS
  if (starting) return;
  if (state === "recording") return stopRecording();
  if (state === "transcribing") { interrupt(); setState("idle"); return; }
  interrupt(); // tapping during thinking/speaking interrupts and starts a new question
  await startRecording();
}

el.talk.addEventListener("click", onTalk);
el.stop.addEventListener("click", () => { interrupt(); setState("idle"); });
el.retry.addEventListener("click", () => {
  const q = turn?.question || el.q.textContent.replace(/^“|”$/g, "");
  if (q) submitText(q);
});

document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || e.repeat) return;
  const tag = e.target?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "BUTTON" || e.target?.isContentEditable) return;
  e.preventDefault();
  onTalk();
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) return;
  const wasRecording = recorder.recording;
  recorder.release(); // stop mic tracks while the tab is hidden
  if (wasRecording) { clearTimeout(autoStop); setState("idle"); }
});


// ---------------------------------------------------------------------- debug
function renderDebug(t) {
  const perceived = t.firstAnswerAt ? Math.round(t.firstAnswerAt - t.t0) : null;
  const tm = t.timings || {};
  el.debug.hidden = false;
  el.debug.textContent = [
    `request_id        ${t.requestId}`,
    `stt_ms            ${t.sttMs ?? "-"}`,
    `llm_first_token   ${tm.llm_ttft_ms ?? "-"} ms`,
    `first_audio (srv) ${tm.first_audio_ms ?? "-"} ms`,
    `first_audio (ui)  ${perceived ?? "-"} ms   (from end of question)`,
    `total (srv)       ${tm.total_ms ?? "-"} ms`,
    `fillers played    ${t.fillersPlayed}`,
    `fallback model    ${t.fallbackUsed ? "yes" : "no"}`,
  ].join("\n");
}

// ---------------------------------------------------------------------- boot
function isInAppBrowser() {
  return /FBAN|FBAV|Instagram|WhatsApp|LinkedInApp|Line\/|; wv\)|Snapchat|MicroMessenger|GSA\//i.test(navigator.userAgent);
}

async function boot() {
  queue.init(); // lets us decode filler clips before the first tap
  try {
    const r = await fetch("/api/config");
    if (r.ok) cfg = { ...cfg, ...(await r.json()) };
  } catch { /* defaults are fine */ }
  renderChips();
  filler = new FillerController(queue, { fillers: cfg.fillers, thresholdMs: cfg.filler_threshold_ms });
  filler.load(cfg.apology).catch(() => {});
  if (isInAppBrowser()) el.hint.hidden = false;
  if (!micOk) {
    el.hint.hidden = false;
    setState("idle", "Type your question below");
  } else {
    setState("idle");
  }
}
boot();
