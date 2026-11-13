"""LLM -> chunker -> TTS, emitted as ordered SSE events (plain dicts: {"event", "data"}).

Sentences are synthesized in parallel (bounded by a semaphore) but delivered strictly in
sequence order, with exactly one audio / tts_fallback event per sentence.
"""
import asyncio
import base64
import logging
import secrets
import threading
import time
from typing import AsyncIterator, Awaitable, Callable

from .chunker import SentenceChunker
from .guards import OpenerFilter
from .llm import LLMDelta, LLMProvider, LLMUsage
from .logging_conf import log_fields
from .tts import TTSProvider, clean_text

logger = logging.getLogger(__name__)

SPOKEN_APOLOGY = "Sorry, I lost my train of thought. Could you ask that again?"
_END = object()


class _Turn:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()
        self.ttft_ms: int | None = None
        self.first_audio_ms: int | None = None
        self.usage = LLMUsage()
        self.model = ""
        self.fallback_used = False
        self.error: Exception | None = None
        self.got_token = False
        self.tts_chars = 0
        self.sentences = 0
        self.tts_failures = 0
        self.words = 0
        self.truncated = False
        self.cancel = threading.Event()  # set on teardown; per-attempt events chain off it
        self.attempt_cancel: threading.Event | None = None
        self.tasks: list[asyncio.Task] = []

    def ms(self) -> int:
        return int((time.perf_counter() - self.t0) * 1000)


def _swallow(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()


class Pipeline:
    def __init__(
        self,
        *,
        llm: LLMProvider,
        tts: TTSProvider,
        fallback_llm: LLMProvider | None = None,
        tts_concurrency: int = 3,
        retry_delay_s: float = 0.3,
        first_token_timeout_s: float = 3.5,
        max_answer_words: int = 120,
    ) -> None:
        self.llm = llm
        self.fallback_llm = fallback_llm
        self.tts = tts
        self.tts_concurrency = tts_concurrency
        self.retry_delay_s = retry_delay_s
        self.first_token_timeout_s = first_token_timeout_s
        self.max_answer_words = max_answer_words

    # ------------------------------------------------------------------ public
    async def run(
        self,
        question: str,
        history: list[dict],
        system: list[dict],
        *,
        ip_hash: str = "",
        stt_ms: int | None = None,
        is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    ) -> AsyncIterator[dict]:
        st = _Turn()
        request_id = "r_" + secrets.token_hex(4)
        outcome = "aborted"
        messages = [*history, {"role": "user", "content": question}]
        out_q: asyncio.Queue = asyncio.Queue()
        sent_q: asyncio.Queue = asyncio.Queue()

        yield {"event": "meta", "data": {"request_id": request_id}}
        consumer = asyncio.create_task(self._consume(st, system, messages, out_q, sent_q))
        emitter = asyncio.create_task(self._emit(st, out_q, sent_q))
        for t in (consumer, emitter):
            t.add_done_callback(_swallow)
        last_check = time.monotonic()
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(out_q.get(), 1.0)
                except asyncio.TimeoutError:
                    ev = None
                # Poll for a vanished client about once a second, even while events keep flowing.
                if is_disconnected is not None and time.monotonic() - last_check >= 1.0:
                    last_check = time.monotonic()
                    if await is_disconnected():
                        return
                if ev is None:
                    continue
                if ev is _END:
                    break
                yield ev
            await asyncio.gather(consumer, emitter)

            if st.error is not None:
                before_tokens = not st.got_token
                outcome = "error"
                yield {
                    "event": "error",
                    "data": {
                        "code": "llm_unavailable" if before_tokens else "llm_interrupted",
                        "message": "The answer service is unavailable right now."
                        if before_tokens
                        else "The answer was cut short.",
                        "spoken": SPOKEN_APOLOGY if before_tokens else "",
                    },
                }
            else:
                outcome = "ok"
                yield {
                    "event": "done",
                    "data": {
                        "timings": {
                            "llm_ttft_ms": st.ttft_ms,
                            "first_audio_ms": st.first_audio_ms,
                            "total_ms": st.ms(),
                        },
                        "model": st.model,
                        "fallback_used": st.fallback_used,
                        "truncated": st.truncated,
                    },
                }
        finally:
            st.cancel.set()
            if st.attempt_cancel is not None:
                st.attempt_cancel.set()
            for t in (consumer, emitter, *st.tasks):
                if not t.done():
                    t.cancel()
            log_fields(
                logger,
                "turn",
                request_id=request_id,
                ip=ip_hash,
                question=question,
                outcome=outcome,
                stt_ms=stt_ms,
                llm_ttft_ms=st.ttft_ms,
                first_audio_ms=st.first_audio_ms,
                total_ms=st.ms(),
                input_tokens=st.usage.input_tokens,
                output_tokens=st.usage.output_tokens,
                cache_read_tokens=st.usage.cache_read_tokens,
                cache_write_tokens=st.usage.cache_write_tokens,
                tts_chars=st.tts_chars,
                sentences=st.sentences,
                tts_failures=st.tts_failures,
                words=st.words,
                truncated=st.truncated,
                model=st.model,
                fallback_used=st.fallback_used,
                error=repr(st.error) if st.error else None,
            )

    # --------------------------------------------------------------- internals
    async def _stream_llm(self, st: _Turn, system: list[dict], messages: list[dict]) -> AsyncIterator[str]:
        attempts: list[tuple[LLMProvider, float, bool]] = [
            (self.llm, 0.0, False),
            (self.llm, self.retry_delay_s, False),
        ]
        if self.fallback_llm is not None:
            attempts.append((self.fallback_llm, 0.0, True))

        last: Exception | None = None
        for provider, delay, is_fallback in attempts:
            if delay:
                await asyncio.sleep(delay)
            cancel = threading.Event()
            st.attempt_cancel = cancel
            it = provider.astream(system, messages, cancel).__aiter__()
            try:
                while True:
                    try:
                        if st.got_token:
                            item = await it.__anext__()
                        else:
                            item = await asyncio.wait_for(it.__anext__(), self.first_token_timeout_s)
                    except StopAsyncIteration:
                        break
                    if isinstance(item, LLMUsage):
                        st.usage = item
                        continue
                    if isinstance(item, LLMDelta) and item.text:
                        if not st.got_token:
                            st.got_token = True
                            st.ttft_ms = st.ms()
                        yield item.text
                if not st.got_token:
                    raise RuntimeError("empty LLM response")
                st.model = provider.model_id
                st.fallback_used = is_fallback
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if st.got_token:
                    raise  # mid-stream failure: can't restart without repeating words
                last = exc
                log_fields(
                    logger,
                    "llm attempt failed",
                    logging.WARNING,
                    model=provider.model_id,
                    error=repr(exc),
                )
            finally:
                cancel.set()
                aclose = getattr(it, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:
                        pass
        raise RuntimeError("all LLM attempts failed") from last

    async def _consume(self, st: _Turn, system, messages, out_q: asyncio.Queue, sent_q: asyncio.Queue) -> None:
        sem = asyncio.Semaphore(self.tts_concurrency)
        chunker = SentenceChunker()
        seq = 0

        async def synth(text: str) -> bytes | None:
            async with sem:
                return await self.tts.synth(text)

        def submit(sentences: list[str]) -> None:
            nonlocal seq
            for raw in sentences:
                text = clean_text(raw)
                if not text:
                    continue
                task = asyncio.create_task(synth(text))
                task.add_done_callback(_swallow)
                st.tasks.append(task)
                st.tts_chars += len(text)
                st.words += len(text.split())
                st.sentences += 1
                sent_q.put_nowait((seq, text, task))
                seq += 1

        opener = OpenerFilter()

        def push(delta: str) -> None:
            if delta:
                out_q.put_nowait({"event": "text", "data": {"delta": delta}})
                submit(chunker.feed(delta))

        stream = self._stream_llm(st, system, messages)
        try:
            async for delta in stream:
                push(opener.feed(delta))
                if st.words >= self.max_answer_words:
                    # Too long for a spoken answer: stop at the sentence boundary we just passed.
                    st.truncated = True
                    break
            if not st.truncated:
                push(opener.flush())
                submit(chunker.flush())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            st.error = exc
            if st.got_token:
                push(opener.flush())
                submit(chunker.flush())
        finally:
            await stream.aclose()
            sent_q.put_nowait(_END)

    async def _emit(self, st: _Turn, out_q: asyncio.Queue, sent_q: asyncio.Queue) -> None:
        try:
            while True:
                item = await sent_q.get()
                if item is _END:
                    return
                seq, text, task = item
                try:
                    audio = await task
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("tts task crashed")
                    audio = None
                if st.first_audio_ms is None:
                    st.first_audio_ms = st.ms()
                if audio:
                    out_q.put_nowait(
                        {
                            "event": "audio",
                            "data": {
                                "seq": seq,
                                "text": text,
                                "mime": "audio/mpeg",
                                "b64": base64.b64encode(audio).decode("ascii"),
                            },
                        }
                    )
                else:
                    st.tts_failures += 1
                    out_q.put_nowait({"event": "tts_fallback", "data": {"seq": seq, "text": text}})
        finally:
            out_q.put_nowait(_END)
