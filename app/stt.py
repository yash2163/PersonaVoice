"""STT provider interface + Groq Whisper implementation."""
import logging
import re
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_HALLUCINATIONS = {"thank you", "thanks", "thanks for watching", "thank you for watching", "you", "bye"}
_MIME = {"webm": "audio/webm", "mp4": "audio/mp4", "m4a": "audio/mp4", "ogg": "audio/ogg", "wav": "audio/wav", "mp3": "audio/mpeg"}


class NoSpeechError(Exception):
    """Nothing usable was heard (empty or a known silence hallucination)."""


class STTUnavailable(Exception):
    """The STT service failed or rate-limited."""


class STTProvider(Protocol):
    async def transcribe(self, audio: bytes, ext: str, duration_ms: int | None = None) -> str: ...


def _is_hallucination(text: str) -> bool:
    return re.sub(r"[^a-z ]", "", text.lower()).strip() in _HALLUCINATIONS


class GroqWhisperSTT:
    SHORT_CLIP_MS = 1500
    SHORT_CLIP_BYTES = 8_000  # fallback guess when the client sends no duration

    def __init__(self, client: httpx.AsyncClient, api_key: str, model: str, prompt: str):
        self._client = client
        self._key = api_key
        self._model = model
        self._prompt = prompt

    async def transcribe(self, audio: bytes, ext: str, duration_ms: int | None = None) -> str:
        if not self._key:
            raise STTUnavailable("GROQ_API_KEY is not configured")
        ext = ext.lower().lstrip(".") or "webm"
        try:
            resp = await self._client.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {self._key}"},
                files={"file": (f"audio.{ext}", audio, _MIME.get(ext, "application/octet-stream"))},
                data={
                    "model": self._model,
                    "language": "en",
                    "temperature": "0",
                    "response_format": "json",
                    "prompt": self._prompt,
                },
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            raise STTUnavailable(f"groq request failed: {exc!r}") from exc
        if resp.status_code != 200:
            raise STTUnavailable(f"groq returned {resp.status_code}: {resp.text[:200]}")
        text = (resp.json().get("text") or "").strip()

        short = duration_ms < self.SHORT_CLIP_MS if duration_ms is not None else len(audio) < self.SHORT_CLIP_BYTES
        if len(text) < 2 or (short and _is_hallucination(text)):
            raise NoSpeechError(text)
        return text
