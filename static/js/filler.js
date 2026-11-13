// Filler logic: pre-synthesized clips played only when the answer is slow.
// Rules: threshold timer from the start of transcribing / text submit; never once answer
// audio has been queued; at most two per turn; avoid the last three used.

export class FillerController {
  constructor(queue, { fillers = [], thresholdMs = 800, holdingMs = 3500, maxPerTurn = 2 } = {}) {
    this.queue = queue;
    this.manifest = fillers;
    this.thresholdMs = thresholdMs;
    this.holdingMs = holdingMs;
    this.maxPerTurn = maxPerTurn;
    this.clips = []; // {id, text, bufferPromise}
    this.apology = null; // bufferPromise
    this.recent = [];
    this._reset();
  }

  /** Fetch clips (and optional apology clip) and start decoding. Needs the AudioContext. */
  async load(apologyEntry) {
    const fetchBuf = async (url) => {
      const r = await fetch(url);
      if (!r.ok) throw new Error(url);
      return this.queue.decode(await r.arrayBuffer());
    };
    this.clips = this.manifest.map((f) => ({ id: f.id, text: f.text, bufferPromise: fetchBuf(f.url).catch(() => null) }));
    if (apologyEntry?.url) this.apology = fetchBuf(apologyEntry.url).catch(() => null);
  }

  _reset() {
    this.turn = 0;
    this.played = 0;
    this.answerQueued = false;
    this.live = false;
    clearTimeout(this._timer);
    this._timer = null;
  }

  startTurn() {
    this._reset();
    this.live = true;
    this._arm(this.thresholdMs);
  }

  /** Call when the first answer audio (or fallback speech) is queued. */
  noteAnswer() {
    this.answerQueued = true;
    clearTimeout(this._timer);
  }

  cancelTurn() {
    this.live = false;
    clearTimeout(this._timer);
  }

  _arm(ms) {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this._fire(), ms);
  }

  _fire() {
    if (!this.live || this.answerQueued || this.played >= this.maxPerTurn || !this.clips.length) return;
    const pool = this.clips.filter((c) => !this.recent.includes(c.id));
    const candidates = pool.length ? pool : this.clips;
    const clip = candidates[Math.floor(Math.random() * candidates.length)];
    this.recent = [...this.recent, clip.id].slice(-3);
    this.played++;
    const myTurnPlayed = this.played;
    this.queue.enqueueBuffer("filler", clip.bufferPromise, {
      text: "", // a failed filler is silence, never browser speech
      gapAfterMs: 120, // breathing room before the answer starts
      onEnd: () => {
        // Still nothing 3.5 s after the filler ended? Allow one more holding filler.
        if (this.live && !this.answerQueued && myTurnPlayed < this.maxPerTurn) this._arm(this.holdingMs);
      },
    });
  }
}
