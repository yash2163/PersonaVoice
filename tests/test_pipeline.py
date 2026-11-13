import asyncio
import random
import threading

from app.llm import LLMDelta, LLMUsage
from app.pipeline import SPOKEN_APOLOGY, Pipeline

TEXT = (
    "Alright, here is the short version. I build agents that talk to real systems. "
    "One example is the AWS harness I wrote. It cut manual steps a lot. "
    "That is what I enjoy most about this work."
)


class FakeLLM:
    def __init__(self, text=TEXT, model_id="fake-llm", fail_times=0, delay=0.002, fail_after_tokens=None):
        self.text, self.model_id, self.fail_times, self.delay = text, model_id, fail_times, delay
        self.fail_after_tokens = fail_after_tokens
        self.calls = 0
        self.last_cancel: threading.Event | None = None

    async def astream(self, system, messages, cancel):
        self.calls += 1
        self.last_cancel = cancel
        if self.calls <= self.fail_times:
            raise RuntimeError("boom")
        words = self.text.split(" ")
        for i, w in enumerate(words):
            if self.fail_after_tokens is not None and i >= self.fail_after_tokens:
                raise RuntimeError("mid-stream boom")
            await asyncio.sleep(self.delay)
            yield LLMDelta(w + (" " if i < len(words) - 1 else ""))
        yield LLMUsage(10, 20, 5, 0)


class FakeTTS:
    voice_id = "fake"

    def __init__(self, fail_on=None, seed=1):
        self.fail_on, self.rng = fail_on, random.Random(seed)
        self.in_flight = self.max_in_flight = 0

    async def synth(self, text):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.rng.uniform(0.001, 0.05))  # later sentences may finish first
            if self.fail_on and self.fail_on in text:
                return None
            return text.encode()
        finally:
            self.in_flight -= 1


async def collect(pipeline, question="q", history=None):
    return [ev async for ev in pipeline.run(question, history or [], [{"text": "sys"}])]


def run(coro):
    return asyncio.run(coro)


def test_event_order_and_one_event_per_sentence():
    tts = FakeTTS()
    events = run(collect(Pipeline(llm=FakeLLM(), tts=tts, tts_concurrency=3)))
    names = [e["event"] for e in events]
    assert names[0] == "meta" and names[-1] == "done"
    audio = [e["data"] for e in events if e["event"] in ("audio", "tts_fallback")]
    assert [a["seq"] for a in audio] == list(range(len(audio)))  # in order, no gaps
    assert len(audio) >= 3
    joined = " ".join(a["text"] for a in audio)
    assert joined.replace("  ", " ") == TEXT
    assert "".join(e["data"]["delta"] for e in events if e["event"] == "text") == TEXT
    assert tts.max_in_flight <= 3
    done = events[-1]["data"]
    assert done["timings"]["first_audio_ms"] is not None and done["fallback_used"] is False


def test_failed_sentence_becomes_tts_fallback_in_place():
    events = run(collect(Pipeline(llm=FakeLLM(), tts=FakeTTS(fail_on="AWS harness"))))
    audio = [e for e in events if e["event"] in ("audio", "tts_fallback")]
    assert [e["data"]["seq"] for e in audio] == list(range(len(audio)))
    fallbacks = [e for e in audio if e["event"] == "tts_fallback"]
    assert len(fallbacks) == 1 and "AWS harness" in fallbacks[0]["data"]["text"]
    assert "b64" not in fallbacks[0]["data"]
    assert events[-1]["event"] == "done"


def test_retry_then_success():
    llm = FakeLLM(fail_times=1)
    events = run(collect(Pipeline(llm=llm, tts=FakeTTS(), retry_delay_s=0.01)))
    assert llm.calls == 2 and events[-1]["event"] == "done" and not events[-1]["data"]["fallback_used"]


def test_fallback_model_used_after_retry_fails():
    primary, fb = FakeLLM(fail_times=99), FakeLLM(model_id="nova")
    events = run(collect(Pipeline(llm=primary, fallback_llm=fb, tts=FakeTTS(), retry_delay_s=0.01)))
    assert primary.calls == 2 and fb.calls == 1
    assert events[-1]["event"] == "done"
    assert events[-1]["data"]["fallback_used"] is True and events[-1]["data"]["model"] == "nova"


def test_all_llms_fail_sends_error_with_spoken_apology():
    events = run(collect(Pipeline(llm=FakeLLM(fail_times=99), tts=FakeTTS(), retry_delay_s=0.01)))
    assert events[-1]["event"] == "error"
    assert events[-1]["data"]["code"] == "llm_unavailable"
    assert events[-1]["data"]["spoken"] == SPOKEN_APOLOGY
    assert not any(e["event"] in ("audio", "tts_fallback") for e in events)


def test_mid_stream_failure_delivers_partial_then_error():
    llm = FakeLLM(fail_after_tokens=14)
    events = run(collect(Pipeline(llm=llm, tts=FakeTTS(), retry_delay_s=0.01)))
    assert llm.calls == 1  # no restart once words were already sent
    assert any(e["event"] == "audio" for e in events)
    assert events[-1]["event"] == "error" and events[-1]["data"]["code"] == "llm_interrupted"


def test_first_token_timeout_triggers_fallback():
    class Hung(FakeLLM):
        async def astream(self, system, messages, cancel):
            self.calls += 1
            await asyncio.sleep(10)
            yield LLMDelta("never")

    fb = FakeLLM(model_id="nova")
    p = Pipeline(llm=Hung(), fallback_llm=fb, tts=FakeTTS(), retry_delay_s=0.0, first_token_timeout_s=0.05)
    events = run(collect(p))
    assert events[-1]["event"] == "done" and events[-1]["data"]["model"] == "nova"


def test_cancellation_on_disconnect_stops_everything():
    async def scenario():
        llm = FakeLLM(text="word " * 400, delay=0.01)
        tts = FakeTTS()
        gen = Pipeline(llm=llm, tts=tts).run("q", [], [{"text": "sys"}])
        seen = 0
        async for ev in gen:
            seen += 1
            if ev["event"] == "audio":
                break
        await gen.aclose()
        await asyncio.sleep(0.2)
        assert llm.last_cancel.is_set()
        assert tts.in_flight == 0
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        assert pending == []

    run(scenario())


def test_is_disconnected_stops_generator():
    async def scenario():
        llm = FakeLLM(text="word " * 400, delay=0.05)

        async def gone():
            return True

        events = [ev async for ev in Pipeline(llm=llm, tts=FakeTTS()).run("q", [], [], is_disconnected=gone)]
        assert events[-1]["event"] != "done"

    run(scenario())


def test_over_long_answer_is_cut_at_a_sentence_boundary():
    long_text = " ".join(f"This is sentence number {i} of a very long answer." for i in range(60))
    llm = FakeLLM(text=long_text, delay=0.0005)
    events = run(collect(Pipeline(llm=llm, tts=FakeTTS(), max_answer_words=40)))
    audio = [e["data"]["text"] for e in events if e["event"] in ("audio", "tts_fallback")]
    assert events[-1]["event"] == "done" and events[-1]["data"]["truncated"] is True
    assert all(a.endswith(".") for a in audio)  # clean cut, never mid-sentence
    assert 40 <= sum(len(a.split()) for a in audio) < 80
    assert llm.last_cancel.is_set()


def test_normal_answer_is_not_truncated():
    events = run(collect(Pipeline(llm=FakeLLM(), tts=FakeTTS(), max_answer_words=140)))
    assert events[-1]["data"]["truncated"] is False


def test_leading_filler_is_stripped_from_text_and_audio():
    llm = FakeLLM(text="Sure, I would say my superpower is staying calm under pressure. It helps a lot.")
    events = run(collect(Pipeline(llm=llm, tts=FakeTTS())))
    text = "".join(e["data"]["delta"] for e in events if e["event"] == "text")
    assert text.startswith("I would say my superpower")
    first_audio = next(e for e in events if e["event"] == "audio")
    assert first_audio["data"]["text"].startswith("I would say")
