// MediaRecorder wrapper with a level meter. The mic stream is reused across turns to avoid
// repeated permission prompts, and released when the tab is hidden.

const CANDIDATES = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"];

export function pickMimeType() {
  if (typeof MediaRecorder === "undefined") return null;
  return CANDIDATES.find((t) => MediaRecorder.isTypeSupported?.(t)) || "";
}

export function recordingSupported() {
  return !!(navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined");
}

export class Recorder {
  /** @param {AudioContext|null} ctx shared context, used only for the level meter */
  constructor(getContext) {
    this.getContext = getContext;
    this.stream = null;
    this.rec = null;
    this.chunks = [];
    this.startedAt = 0;
    this._analyser = null;
    this._source = null;
  }

  get recording() { return this.rec?.state === "recording"; }
  get analyser() { return this._analyser; }

  async start() {
    if (!this.stream || !this.stream.active || this.stream.getAudioTracks().every((t) => t.readyState === "ended")) {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
    }
    const mimeType = pickMimeType();
    this.mimeType = mimeType;
    this.ext = (mimeType || "").includes("mp4") ? "mp4" : "webm";
    this.rec = new MediaRecorder(this.stream, mimeType ? { mimeType } : undefined);
    this.chunks = [];
    this.rec.ondataavailable = (e) => { if (e.data?.size) this.chunks.push(e.data); };
    this.rec.start();
    this.startedAt = performance.now();
    this._startMeter();
  }

  /** Resolves with {blob, ext, durationMs}. */
  stop() {
    return new Promise((resolve) => {
      const rec = this.rec;
      if (!rec || rec.state === "inactive") return resolve(null);
      const durationMs = Math.round(performance.now() - this.startedAt);
      rec.onstop = () => {
        this._stopMeter();
        const type = rec.mimeType || this.mimeType || "audio/webm";
        resolve({ blob: new Blob(this.chunks, { type }), ext: this.ext, durationMs });
      };
      rec.stop();
    });
  }

  cancel() {
    try { if (this.rec && this.rec.state !== "inactive") { this.rec.onstop = null; this.rec.stop(); } } catch { /* ignore */ }
    this._stopMeter();
  }

  release() {
    this.cancel();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
  }

  _startMeter() {
    const ctx = this.getContext();
    if (!ctx) return;
    try {
      this._source = ctx.createMediaStreamSource(this.stream);
      this._analyser = ctx.createAnalyser();
      this._analyser.fftSize = 256;
      this._analyser.smoothingTimeConstant = 0.6;
      this._source.connect(this._analyser); // not connected to destination: no feedback
    } catch { /* the meter is cosmetic */ }
  }

  _stopMeter() {
    try { this._source?.disconnect(); } catch { /* ignore */ }
    this._source = null;
    this._analyser = null;
  }
}
