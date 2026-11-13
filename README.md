# Yash Rajput voice bot

A web voice bot that answers interview questions the way Yash would. Open the link, tap the mic (or type, or tap a sample question), and hear a spoken answer within a couple of seconds.

**Live:** https://13-204-210-169.sslip.io · add `?debug=1` to print per-turn timings to the browser console (nothing technical is shown on the page).

![Architecture](docs/architecture.png)

Browser (static page, push-to-talk) → one stateless FastAPI container → Amazon Bedrock (Claude Haiku) for the answer, Amazon Polly for speech, Groq Whisper for transcription. A question goes up as recorded audio (transcribed first) or typed text. The answer comes back as one SSE stream of text deltas and per-sentence mp3 chunks; a client-side queue plays them in order.

## Decisions and tradeoffs

| Decision | Choice | Why | Rejected alternative |
|---|---|---|---|
| Knowledge | Full profile (Markdown, ~2–4k tokens) in the system prompt, with Bedrock prompt caching | No retrieval misses, no extra hop; cached prefix is cheap and fast | RAG: adds latency and failure modes, only pays off at tens of thousands of tokens |
| Profile format | Markdown with headed sections | Stories and nuance read better than JSON | JSON: fine for facts, awkward for narrative |
| Turn model | Half-duplex: tap to talk, tap to stop, bot answers | Fits a 1-day build; no echo cancellation or barge-in detection needed | Full-duplex live call |
| Latency strategy | Stream LLM tokens, cut into sentences, synthesize each sentence as it completes | The first sentence plays while the rest is still generating | Synthesize the full answer at the end |
| Waiting | No spoken filler. The page shows "Yash is thinking." and the voice line animates | Pre-recorded clips ("Hmm, okay.") were built and tested, but sounded unrelated to the question and were removed | A contextual "filler LLM" (see the option below); not built |
| No overlapping voices | One client-side sequential playback queue; server-side `asyncio.Semaphore` caps concurrent TTS | Audio plays in the browser, so ordering belongs there | Server-side semaphore to stop overlap |
| LLM | Claude Haiku on Bedrock (ConverseStream), model ID from env | Fast, strong persona writing, paid from AWS credits | Larger models: slower for no visible gain |
| TTS | Amazon Polly generative voice Arjun (Indian English, served from `us-east-1` via `POLLY_REGION`; neural Matthew in `ap-south-1` is the faster fallback), sent as SSML (rate 94%, short pauses after commas, a longer pause after each sentence; tune with `TTS_RATE_PCT`, `TTS_COMMA_PAUSE_MS`, `TTS_SENTENCE_PAUSE_MS`; regenerate the apology clip with scripts/gen_apology.py after changing); browser `speechSynthesis` as fallback | Natural voice, covered by credits, same cloud and IAM | ElevenLabs free tier: ~10 min a month, testers could exhaust it |
| STT | Groq `whisper-large-v3-turbo` on recorded audio | Fast, accurate on Indian English, works in every browser | Web Speech API: no Firefox, uneven Safari; Transcribe streaming: too much plumbing |
| Transport | POST + Server-Sent Events over plain HTTP | Works through most proxies and hosts; one stream carries text and audio | WebSockets: not supported on every host |

## Option: a "filler LLM" for the wait

The wait between the end of a question and the start of the answer is about 1 to 3 seconds. This build does not fill it with spoken audio. A first version played pre-recorded clips ("Hmm, okay.") after 0.8 s, but in testing they sounded unrelated to the question, like random noise, so they were removed. While waiting, the page shows "Yash is thinking." and the voice line animates.

The alternative I'd add next is a **contextual filler**, made by a second, tiny model:

1. When the question arrives, start the main answer as usual and, in parallel, ask a very small, fast model (for example Amazon Nova Micro) for a 3 to 6 word acknowledgement that fits this question ("Good one, let me think about my strengths.").
2. Voice it and play it only if it is ready before the real first sentence and the real answer hasn't started. Otherwise drop it.
3. Make the main answer's first sentence continue from it, or at least never repeat it.

| | Pre-recorded clips (built, removed) | Contextual filler LLM (not built) |
|---|---|---|
| Related to the question | No | Yes |
| Extra cost per turn | none | one tiny model call plus one short voice clip, well under a tenth of a cent |
| Risk | sounds random | it may promise something the answer doesn't deliver, so it must stay generic about the topic and never state facts |
| Added delay | the real answer waits behind the clip | none if dropped when late; the clip is short |
| Complexity | low | a second stream to race against the first, and a rule for when to drop it |

It is the first thing to try if first-audio times under the generative voice feel too long.

## Reliability

| Failure | Behaviour |
|---|---|
| Bedrock throttled/erroring | Retry once after 300 ms → fallback model → `error` event + pre-recorded apology clip |
| Bedrock silent (no first token in 3.5 s) | Treated as a failed attempt, same ladder (normal first token is ~0.55 s; a stall was seen once at ~9 s in testing) |
| Polly fails for a sentence | `tts_fallback` event; the browser speaks that sentence |
| Groq fails or hears nothing | Friendly toast, focus moves to the text box |
| Mic denied / in-app browser | Text mode plus a hint to open in Chrome or Safari |
| Network drop mid-stream | Plays what arrived, shows Retry |
| Rate limit | 429 with a friendly written and spoken message |

Spend is capped three ways: per-request limits (question length, history size, audio size, `maxTokens`), per-IP rate limits (20/min and 300/day on `/api/ask`), and AWS Budgets alerts.

## Measured latency and quality

Measured locally against the real services (Bedrock Haiku 4.5, Groq, and Polly neural/Matthew; the Arjun generative voice adds roughly 1 s to each sentence's synthesis, so first audio is now about 1.8–3 s in a spot check of 3 turns) with `scripts/eval.py`: 54 turns (26 main-set + 28 edge-case). Network and speech-to-text time are not included in first audio.

| Metric | Result |
|---|---|
| First audio, from the question reaching the server | median ≈ 1.0 s, p90 ≈ 1.4 s (3 of 54 turns slower; see below) |
| LLM first token | median ≈ 0.55 s, p90 ≈ 0.7 s |
| Speech-to-text (Groq whisper-large-v3-turbo) | ≈ 0.25–0.4 s per question |
| Answer length | median 52–69 words; hard cap 120 words (cut at a sentence boundary) |
| Groundedness (strict Opus judge with verified quotes) | 4.73 main set, 4.82 edge cases (target ≥ 4.5) |
| Boundary violations | none |
| Prompt cache hits | 52 of 54 turns |

Slow turns: 3 of 54 had first audio above 2 s, two of them 7–9 s because Bedrock's first token stalled (cause not isolated; no throttling errors at 24 concurrent requests). The first-token timeout is now 3.5 s with an automatic retry, so a stall costs a few seconds, not a failure. Watch `llm_ttft_ms` in the logs during the evaluation window.

## Cost estimate

Measured: about 4,700 cached input tokens, 80 output tokens and 350 spoken characters per turn. At Haiku 4.5 rates ($1 / $5 per million tokens, cache reads $0.10) and Polly neural ($16 per million characters) that is ≈ $0.0065 per turn. Lightsail Micro is ≈ $10/month. 500 evaluation turns is well under $20. (Estimates from memory; verify current pricing.)

## Run locally

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
cp .env.example .env        # fill in AWS keys, BEDROCK_MODEL_ID, POLLY_VOICE_ID, GROQ_API_KEY
.venv/bin/python scripts/gen_apology.py   # once, after choosing the Polly voice (the one pre-recorded clip)
.venv/bin/uvicorn app.main:app --reload --port 8000
# open http://localhost:8000   (localhost counts as a secure context, so the mic works)
```

Tests:
- `.venv/bin/python -m pytest` runs the backend and colour-contrast tests, and `node --test tests/js/sse.test.mjs` the SSE client.
- Browser end-to-end tests (voice, text, stop, interrupt, errors, mobile layout, keyboard) run the real app with fake providers in Chromium, Firefox and WebKit, so they are free and need no keys:
  `uv pip install --python .venv/bin/python -r requirements-e2e.txt && .venv/bin/python -m playwright install chromium firefox webkit && .venv/bin/python -m pytest -m e2e tests/e2e`
Eval: `.venv/bin/python scripts/eval.py --base-url http://localhost:8000` (writes `eval_report.md`).
Streaming check: `curl -N -X POST $URL/api/ask -H 'content-type: application/json' -d '{"question":"What is your superpower?","history":[]}'` must print events gradually.

## Frontend design

The page is one column of plain type with a single memorable element, the voice line: a live waveform that is coral from the microphone while you speak and teal from Yash's actual audio while he answers (a slow wave while thinking). It replaces the usual round mic button with a labelled "Tap to talk" button, which is clearer for non-technical testers. Answers are set in Newsreader, the interface in Instrument Sans; both are self-hosted in `static/fonts/` (SIL Open Font License) so the page has no third-party runtime dependency. Light and dark themes follow the system, colour pairs are checked for WCAG AA in `tests/test_contrast.py`, and the layout is tested down to 360 px wide. A sticky top bar keeps the History button and the "How it's built" link in reach. History opens as a slide-over panel listing the last 20 messages, newest first (kept in the visitor's own browser, clearable). Once a conversation starts, the sample questions shrink to a single scrolling strip so the answer gets the room. A plain-language technical page at `/about/` (`static/about/index.html`) covers the pipeline, design decisions, results, what I'd change and what could be added. Static files are served with `Cache-Control: no-cache` so a redeploy is never hidden by a stale cache.

## Tuning the persona

All persona content lives in `knowledge/` so tuning never touches code: `profile.md` (the master document), `system_prompt.md`, `pronunciations.json` (TTS respellings), `sample_questions.json` (chips). Read the logged questions after testers use it to see which profile gaps to fill.

## Deploy

The demo runs on one small EC2 server (`t3.micro`, Ubuntu 24.04, Mumbai) with an Elastic IP. Caddy terminates HTTPS with a free Let's Encrypt certificate for `<ip-with-dashes>.sslip.io` (browsers only allow the microphone on HTTPS) and proxies to the app, which runs under systemd (`deploy/voicebot.service`, `deploy/Caddyfile`). The server holds no data: conversation history lives in each visitor's browser, and the logs go to the journal.

- Update: `scripts/deploy_ec2.sh <server-ip>` copies the code and restarts the service.
- Settings live in `/etc/yash-voicebot.env` on the server (mode 600), the same names as `.env.example`. systemd does not strip trailing `# comments`, so keep values bare there.
- Bedrock and Polly are reached with the keys in that file. An instance IAM role would be safer and is the first thing to change.
- Cost: about $10 a month for the server plus the public IPv4 address. Stop paying by terminating the instance and releasing the Elastic IP.
- `Dockerfile` is kept for any container host.
