"""Browser test fixtures: the real FastAPI app (real static frontend) with fake LLM/TTS/STT."""
import asyncio
import math
import socket
import struct
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import pytest

pytest.importorskip("playwright")
import uvicorn  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

from app.config import Settings  # noqa: E402
from app.llm import LLMDelta, LLMUsage  # noqa: E402
from app.main import create_app  # noqa: E402
from app.persona import load_persona  # noqa: E402
from app.stt import NoSpeechError  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
MP3 = (ROOT / "static" / "audio" / "apology.mp3").read_bytes()  # a few seconds of real mp3
BROWSERS = ["chromium", "firefox", "webkit"]


@dataclass
class Behaviour:
    """Mutable knobs the tests turn; reset before every test."""
    first_token_delay: float = 0.0
    token_delay: float = 0.01
    tts_delay: float = 0.03
    fail_llm: bool = False
    stt_text: str = "What is your number one superpower?"
    stt_none: bool = False


class FakeLLM:
    model_id = "fake-llm"

    def __init__(self, b: Behaviour):
        self.b = b

    async def astream(self, system, messages, cancel):
        if self.b.fail_llm:
            raise RuntimeError("simulated outage")
        q = messages[-1]["content"]
        text = (
            f"Here is my answer to {q} It goes on for a few sentences so the audio plays for a while. "
            "This is the third sentence of the answer, which keeps playing. And this is the last one."
        )
        await asyncio.sleep(self.b.first_token_delay)
        for word in text.split(" "):
            await asyncio.sleep(self.b.token_delay)
            yield LLMDelta(word + " ")
        yield LLMUsage(10, 20, 0, 0)


class FakeTTS:
    voice_id = "fake-voice"

    def __init__(self, b: Behaviour):
        self.b = b

    async def synth(self, text):
        await asyncio.sleep(self.b.tts_delay)
        return MP3


class FakeSTT:
    def __init__(self, b: Behaviour):
        self.b = b

    async def transcribe(self, audio, ext, duration_ms=None):
        if self.b.stt_none:
            raise NoSpeechError("")
        return self.b.stt_text


class LiveServer:
    def __init__(self, **settings):
        self.behaviour = Behaviour()
        kw = {"rate_limit_ask": "1000/minute", "rate_limit_stt": "1000/minute", **settings}
        s = Settings(_env_file=None, **kw)
        app = create_app(
            s,
            persona=load_persona(s.knowledge_dir, cache=False),
            llm=FakeLLM(self.behaviour),
            tts=FakeTTS(self.behaviour),
            stt=FakeSTT(self.behaviour),
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        self._server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + 10
        while not self._server.started:
            if time.time() > deadline:
                raise RuntimeError("test server did not start")
            time.sleep(0.05)

    def stop(self):
        self._server.should_exit = True
        self._thread.join(timeout=5)


@pytest.fixture(scope="session")
def server():
    s = LiveServer()
    yield s
    s.stop()


@pytest.fixture(autouse=True)
def _reset(request):
    if "server" in request.fixturenames:
        request.getfixturevalue("server").behaviour.__init__()
    yield


@pytest.fixture(scope="session")
def mic_wav(tmp_path_factory):
    """6 s of speech-like audio for Chromium's fake microphone."""
    path = tmp_path_factory.mktemp("mic") / "mic.wav"
    rate = 16000
    frames = bytearray()
    for i in range(rate * 6):
        t = i / rate
        env = 0.5 + 0.5 * math.sin(2 * math.pi * 3.5 * t)
        v = 0.6 * env * (math.sin(2 * math.pi * 180 * t) + 0.6 * math.sin(2 * math.pi * 540 * t))
        frames += struct.pack("<h", int(max(-1, min(1, v)) * 16000))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return str(path)


@pytest.fixture(scope="session")
def pw():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browsers(pw, mic_wav):
    """One browser per engine, launched lazily. Chromium gets a fake microphone."""
    cache = {}

    def get(name):
        if name not in cache:
            args = []
            if name == "chromium":
                args = ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", f"--use-file-for-fake-audio-capture={mic_wav}"]
            cache[name] = getattr(pw, name).launch(headless=True, args=args)
        return cache[name]

    yield get
    for b in cache.values():
        b.close()


class Page:
    """Thin wrapper that records console errors and offers state helpers."""

    def __init__(self, ctx, url):
        self.ctx = ctx
        self.page = ctx.new_page()
        self.errors, self.requests = [], []
        self.page.on("console", lambda m: self.errors.append(m.text) if m.type == "error" else None)
        self.page.on("pageerror", lambda e: self.errors.append(f"pageerror: {e}"))
        self.page.on("request", lambda r: self.requests.append(r) if "/api/" in r.url else None)
        self.url = url

    def goto(self, query=""):
        self.page.goto(self.url + query)
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_function("document.querySelectorAll('.chip').length > 0")
        return self.page

    def state(self):
        return self.page.get_attribute("#talk", "data-state")

    def wait_state(self, s, timeout=20000):
        self.page.wait_for_function("s => document.querySelector('#talk').dataset.state === s", arg=s, timeout=timeout)

    def ask_bodies(self):
        import json
        return [json.loads(r.post_data) for r in self.requests if r.url.endswith("/api/ask")]


@pytest.fixture
def make_page(browsers, server):
    made = []

    def make(browser="chromium", srv=None, permissions=None, **ctx_kw):
        b = browsers(browser)
        if browser == "chromium" and permissions is None:
            permissions = ["microphone"]
        ctx = b.new_context(permissions=permissions or [], **ctx_kw)
        made.append(ctx)
        return Page(ctx, (srv or server).url)

    yield make
    for c in made:
        c.close()
