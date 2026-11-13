"""LLM provider interface + Bedrock ConverseStream implementation."""
import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import AsyncIterator, Protocol

logger = logging.getLogger(__name__)


@dataclass
class LLMDelta:
    text: str


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


LLMItem = LLMDelta | LLMUsage


class LLMProvider(Protocol):
    model_id: str

    def astream(
        self, system: list[dict], messages: list[dict], cancel: threading.Event
    ) -> AsyncIterator[LLMItem]:
        """Yield LLMDelta items as text arrives, then one LLMUsage. Stop early if `cancel` is set."""
        ...


_END = object()


class BedrockLLM:
    """boto3 converse_stream runs in a thread; deltas are pushed onto an asyncio.Queue."""

    def __init__(self, client, model_id: str, temperature: float, max_tokens: int, use_cache: bool = True):
        self._client = client
        self.model_id = model_id
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._use_cache = use_cache

    async def astream(
        self, system: list[dict], messages: list[dict], cancel: threading.Event
    ) -> AsyncIterator[LLMItem]:
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def put(item) -> None:
            try:
                loop.call_soon_threadsafe(q.put_nowait, item)
            except RuntimeError:  # loop already closed
                pass

        sys_blocks = system if self._use_cache else [b for b in system if "cachePoint" not in b]
        bedrock_messages = [{"role": m["role"], "content": [{"text": m["content"]}]} for m in messages]

        def worker() -> None:
            stream = None
            try:
                resp = self._client.converse_stream(
                    modelId=self.model_id,
                    system=sys_blocks,
                    messages=bedrock_messages,
                    inferenceConfig={"temperature": self._temperature, "maxTokens": self._max_tokens},
                )
                stream = resp["stream"]
                for event in stream:
                    if cancel.is_set():
                        break
                    if "contentBlockDelta" in event:
                        text = event["contentBlockDelta"].get("delta", {}).get("text")
                        if text:
                            put(LLMDelta(text))
                    elif "metadata" in event:
                        u = event["metadata"].get("usage", {})
                        put(
                            LLMUsage(
                                u.get("inputTokens", 0),
                                u.get("outputTokens", 0),
                                u.get("cacheReadInputTokens", 0),
                                u.get("cacheWriteInputTokens", 0),
                            )
                        )
                put(_END)
            except Exception as exc:  # forwarded to the consumer
                put(exc)
            finally:
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass

        threading.Thread(target=worker, name="bedrock-stream", daemon=True).start()
        try:
            while True:
                item = await q.get()
                if item is _END:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            cancel.set()
