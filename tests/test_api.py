import json

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.persona import Persona
from app.stt import NoSpeechError
from tests.test_pipeline import FakeLLM, FakeTTS


class FakeSTT:
    def __init__(self, text="hello there", exc=None):
        self.text, self.exc, self.calls = text, exc, []

    async def transcribe(self, audio, ext, duration_ms=None):
        self.calls.append((len(audio), ext, duration_ms))
        if self.exc:
            raise self.exc
        return self.text


def make_client(stt=None, **settings_kw):
    kw = {"rate_limit_ask": "100/minute", "rate_limit_stt": "100/minute", **settings_kw}
    settings = Settings(_env_file=None, **kw)
    persona = Persona("sys prompt", "profile", {}, [], cache=True)
    app = create_app(settings, persona=persona, llm=FakeLLM(), tts=FakeTTS(), stt=stt or FakeSTT())
    return TestClient(app)


def parse_sse(text):
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.split("\n"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_health_and_config():
    with make_client() as c:
        assert c.get("/api/health").json() == {"ok": True, "llm": "fake-llm", "tts": "fake"}
        cfg = c.get("/api/config").json()
        assert cfg["filler_threshold_ms"] == 800 and len(cfg["sample_questions"]) >= 5
        assert "key" not in json.dumps(cfg).lower()


def test_ask_streams_sse_in_contract_shape():
    with make_client() as c:
        r = c.post("/api/ask", json={"question": "What is your superpower?", "history": []})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["cache-control"] == "no-cache" and r.headers["x-accel-buffering"] == "no"
        ev = parse_sse(r.text)
        assert ev[0][0] == "meta" and ev[-1][0] == "done"
        audio = [d for n, d in ev if n == "audio"]
        assert [a["seq"] for a in audio] == list(range(len(audio))) and audio[0]["mime"] == "audio/mpeg"
        assert set(ev[-1][1]["timings"]) == {"llm_ttft_ms", "first_audio_ms", "total_ms"}


def test_ask_validation():
    with make_client() as c:
        bad = [
            {"question": "", "history": []},
            {"question": "x" * 501, "history": []},
            {"question": "hi", "history": [{"role": "assistant", "content": "a"}]},  # must start with user
            {"question": "hi", "history": [{"role": "user", "content": "a"}]},  # must end with assistant
            {"question": "hi", "history": [{"role": "user", "content": "a" * 1501}, {"role": "assistant", "content": "b"}]},
            {"question": "hi", "history": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}] * 4},
        ]
        for body in bad:
            assert c.post("/api/ask", json=body).status_code == 422, body
        ok = {"question": "hi", "history": [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]}
        assert c.post("/api/ask", json=ok).status_code == 200


def test_ask_rate_limit_returns_friendly_429():
    with make_client(rate_limit_ask="2/minute") as c:
        codes = [c.post("/api/ask", json={"question": "hi", "history": []}).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        body = c.post("/api/ask", json={"question": "hi"}).json()
        assert body["error"] == "rate_limited" and body["spoken"]


def test_transcribe_ok_and_filename_extension():
    stt = FakeSTT()
    with make_client(stt) as c:
        r = c.post("/api/transcribe", files={"audio": ("rec.mp4", b"x" * 100, "audio/mp4")}, data={"duration_ms": "2500"})
        assert r.status_code == 200 and r.json()["text"] == "hello there" and "ms" in r.json()
        assert stt.calls == [(100, "mp4", 2500)]


def test_transcribe_nothing_heard_is_422():
    with make_client(FakeSTT(exc=NoSpeechError("."))) as c:
        r = c.post("/api/transcribe", files={"audio": ("a.webm", b"x" * 100, "audio/webm")})
        assert r.status_code == 422


def test_transcribe_too_large_is_413():
    with make_client(max_audio_bytes=1000) as c:
        r = c.post("/api/transcribe", files={"audio": ("a.webm", b"x" * 1001, "audio/webm")})
        assert r.status_code == 413


def test_static_index_served_at_root():
    with make_client() as c:
        assert c.get("/").status_code == 200
