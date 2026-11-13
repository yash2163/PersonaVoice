// The voice line: a row of bars driven by real audio. Coral from the mic while you speak,
// teal from the playback analyser while Yash answers, a slow travelling wave while thinking.
// If no analyser data is available (browser speech fallback) it falls back to gentle synthetic motion.

const REDUCED = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export class VoiceLine {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.mode = "idle"; // idle | user | bot | thinking
    this.analyser = null;
    this.bars = [];
    this.freq = null;
    this.colors = {};
    this._raf = 0;
    this._t0 = performance.now();
    this._resize = this._resize.bind(this);
    this._frame = this._frame.bind(this);
    new ResizeObserver(this._resize).observe(canvas);
    window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", () => this._readColors());
    document.addEventListener("visibilitychange", () => (document.hidden ? this._stop() : this._start()));
    this._resize();
    this._start();
  }

  /** @param {"idle"|"user"|"bot"|"thinking"} mode @param {AnalyserNode|null} analyser */
  setMode(mode, analyser = null) {
    this.mode = mode;
    this.analyser = analyser;
    if (analyser) this.freq = new Uint8Array(analyser.frequencyBinCount);
    this._readColors();
    this._start();
  }

  _readColors() {
    const cs = getComputedStyle(this.canvas);
    this.colors = {
      user: cs.getPropertyValue("--voice-user").trim() || "#B8361B",
      bot: cs.getPropertyValue("--voice-bot").trim() || "#0B7A75",
      quiet: cs.getPropertyValue("--voice-quiet").trim() || "rgba(86,104,115,.38)",
    };
  }

  _resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const { width, height } = this.canvas.getBoundingClientRect();
    this.w = Math.max(1, Math.floor(width));
    this.h = Math.max(1, Math.floor(height));
    this.canvas.width = this.w * dpr;
    this.canvas.height = this.h * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.barW = this.w < 480 ? 3 : 4;
    this.gap = this.w < 480 ? 4 : 5;
    const n = Math.max(12, Math.floor((this.w + this.gap) / (this.barW + this.gap)));
    this.bars = new Array(n).fill(0);
    this._readColors();
    this._draw(performance.now());
  }

  _start() { if (!this._raf && !document.hidden) this._raf = requestAnimationFrame(this._frame); }
  _stop() { cancelAnimationFrame(this._raf); this._raf = 0; }

  _frame(now) {
    this._raf = 0;
    this._draw(now);
    // Idle with reduced motion is a still line: stop the loop until the mode changes.
    if (!(REDUCED && this.mode === "idle") && !document.hidden) this._raf = requestAnimationFrame(this._frame);
  }

  _targets(now) {
    const n = this.bars.length;
    const t = (now - this._t0) / 1000;
    const out = new Array(n);
    const live = (this.mode === "user" || this.mode === "bot") && this.analyser;
    let energy = 0;
    if (live) {
      this.analyser.getByteFrequencyData(this.freq);
      const c = (n - 1) / 2;
      for (let i = 0; i < n; i++) {
        const d = Math.abs(i - c) / c; // 0 centre .. 1 edge
        const bin = 1 + Math.floor(Math.pow(d, 1.35) * Math.min(48, this.freq.length - 2));
        const v = this.freq[bin] / 255;
        out[i] = Math.min(1, v * (1.15 - d * 0.35));
        energy += out[i];
      }
    }
    if (live && energy > 0.02) return out;
    for (let i = 0; i < n; i++) {
      if (this.mode === "thinking") out[i] = REDUCED ? 0.16 : 0.1 + 0.16 * (0.5 + 0.5 * Math.sin(t * 2.4 - i * 0.32));
      else if (this.mode === "bot") out[i] = REDUCED ? 0.2 : 0.14 + 0.3 * Math.abs(Math.sin(t * 5.1 + i * 0.7) * Math.sin(t * 2.3 + i * 0.21)); // speech-fallback stand-in
      else if (this.mode === "user") out[i] = 0.06; // mic is open but quiet
      else out[i] = REDUCED ? 0.05 : 0.045 + 0.025 * Math.sin(t * 0.9 + i * 0.4);
    }
    return out;
  }

  _draw(now) {
    const { ctx, w, h, bars, barW, gap } = this;
    const targets = this._targets(now);
    ctx.clearRect(0, 0, w, h);
    const n = bars.length;
    const total = n * barW + (n - 1) * gap;
    const x0 = 0 + (w - total) / 2;
    const color = this.mode === "user" ? this.colors.user : this.mode === "idle" ? this.colors.quiet : this.colors.bot;
    ctx.fillStyle = color;
    ctx.globalAlpha = this.mode === "thinking" ? 0.55 : 1;
    const maxH = h * 0.92, minH = barW;
    for (let i = 0; i < n; i++) {
      const target = targets[i];
      bars[i] += (target - bars[i]) * (target > bars[i] ? 0.55 : 0.14); // quick attack, slow release
      const bh = Math.max(minH, bars[i] * maxH);
      const x = x0 + i * (barW + gap), y = (h - bh) / 2;
      ctx.beginPath();
      if (ctx.roundRect) ctx.roundRect(x, y, barW, bh, barW / 2); else ctx.rect(x, y, barW, bh);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
}
