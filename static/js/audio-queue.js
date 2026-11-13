// The single answer to "no two voices at once": everything audible goes through this queue
// and plays strictly one item at a time. A generation counter makes stop() safe against
// stale promises.

const delay = (ms) => new Promise((r) => setTimeout(r, ms));

export class AudioQueue {
  constructor() {
    this.ctx = null;
    this.analyser = null;
    this._items = [];
    this._running = false;
    this._gen = 0;
    this._src = null;
    this._utter = null;
    this._voice = null;
    this.current = null;
    this.onItemStart = null; // (item) => void
    this.onDrain = null; // () => void  (queue empty and nothing playing)
    this._initVoices();
  }

  /** Create the shared context (suspended until a gesture); enough for decodeAudioData. */
  init() {
    if (!this.ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (AC) {
        this.ctx = new AC();
        // Everything audible passes through this analyser, which drives the voice line.
        this.analyser = this.ctx.createAnalyser();
        this.analyser.fftSize = 256;
        this.analyser.smoothingTimeConstant = 0.6;
        this.analyser.connect(this.ctx.destination);
      }
    }
    return this.ctx;
  }

  /** Call from a user gesture. Resumes the shared AudioContext (required on iOS). */
  async unlock() {
    if (!this.init()) return;
    if (this.ctx.state !== "running") {
      try { await this.ctx.resume(); } catch { /* ignore */ }
    }
    if (!this._unlocked) {
      const buf = this.ctx.createBuffer(1, 1, 22050);
      const src = this.ctx.createBufferSource();
      src.buffer = buf;
      src.connect(this.ctx.destination);
      src.start(0);
      this._unlocked = true;
    }
  }

  get busy() { return this._running || this._items.length > 0; }

  /** Decode now (so there is no gap at play time); returns a promise of AudioBuffer|null. */
  decode(arrayBuffer) {
    if (!this.ctx) return Promise.resolve(null);
    return new Promise((resolve) => {
      // Callback form works on old Safari too; promise form on the rest.
      try {
        const p = this.ctx.decodeAudioData(arrayBuffer, resolve, () => resolve(null));
        if (p && p.catch) p.then(resolve, () => resolve(null));
      } catch { resolve(null); }
    });
  }

  enqueue(item) {
    this._items.push(item);
    this._run();
  }

  /** kind: "answer" | "apology". `bufferPromise` resolves to an AudioBuffer or null. */
  enqueueBuffer(kind, bufferPromise, { text = "", gapAfterMs = 0, onEnd = null } = {}) {
    this.enqueue({
      kind,
      text,
      play: async (gen) => {
        const buf = await bufferPromise;
        if (gen !== this._gen) return;
        if (buf) await this._playBuffer(buf);
        else if (text) await this._speak(text, gen); // decode failed: still say it
        if (gen !== this._gen) return;
        if (gapAfterMs) await delay(gapAfterMs);
        if (onEnd && gen === this._gen) onEnd();
      },
    });
  }

  enqueueSpeech(text, kind = "answer") {
    this.enqueue({ kind, text, play: (gen) => this._speak(text, gen) });
  }

  stop() {
    this._gen++;
    this._items = [];
    this._running = false;
    this.current = null;
    try { this._src?.stop(); } catch { /* already stopped */ }
    this._src = null;
    try { window.speechSynthesis?.cancel(); } catch { /* ignore */ }
  }

  // ------------------------------------------------------------------ internals
  async _run() {
    if (this._running) return;
    this._running = true;
    const gen = this._gen;
    while (this._items.length && gen === this._gen) {
      const item = this._items.shift();
      this.current = item;
      try { this.onItemStart?.(item); } catch (e) { console.warn(e); }
      try { await item.play(gen); } catch (e) { console.warn("playback item failed", e); }
    }
    if (gen === this._gen) {
      this._running = false;
      this.current = null;
      this.onDrain?.();
    }
  }

  _playBuffer(buf) {
    return new Promise((resolve) => {
      const src = this.ctx.createBufferSource();
      src.buffer = buf;
      src.connect(this.analyser || this.ctx.destination);
      this._src = src;
      src.onended = () => { if (this._src === src) this._src = null; resolve(); };
      src.start(0);
    });
  }

  _initVoices() {
    const synth = window.speechSynthesis;
    if (!synth) return;
    const pick = () => {
      const voices = synth.getVoices();
      if (!voices.length) return;
      const en = voices.filter((v) => /^en[-_]/i.test(v.lang));
      this._voice =
        en.find((v) => /male|daniel|alex|james|google uk english male|rishi|aaron|fred/i.test(v.name) && !/female/i.test(v.name)) ||
        en.find((v) => /^en[-_]IN/i.test(v.lang)) ||
        en[0] || null;
    };
    pick();
    synth.addEventListener?.("voiceschanged", pick); // Firefox/Chrome load voices late
  }

  _speak(text, gen) {
    return new Promise((resolve) => {
      const synth = window.speechSynthesis;
      if (!synth || !text.trim() || gen !== this._gen) return resolve();
      const u = new SpeechSynthesisUtterance(text);
      if (this._voice) { u.voice = this._voice; u.lang = this._voice.lang; } else u.lang = "en-US";
      this._utter = u; // keep a reference: some browsers GC the utterance and never fire onend
      let settled = false;
      const finish = () => { if (!settled) { settled = true; clearTimeout(timer); resolve(); } };
      // iOS sometimes never fires onend; don't let the whole queue hang on it.
      const timer = setTimeout(finish, Math.min(20000, 3000 + text.length * 90));
      u.onend = finish;
      u.onerror = finish;
      synth.speak(u);
    });
  }
}
