"""TTS provider interface, Amazon Polly implementation, and text normalisation."""
import asyncio
import logging
import re
from typing import Protocol

logger = logging.getLogger(__name__)

_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0000FE0F\U0000200D]+"
)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.MULTILINE)
_HEADING = re.compile(r"^\s*(?:#{1,6}|>)\s*", re.MULTILINE)
_EMPHASIS = re.compile(r"[*`~]+|(?<!\w)_+|_+(?!\w)")
_WS = re.compile(r"\s+")


def clean_text(text: str) -> str:
    """Strip Markdown symbols, bullets and emoji; collapse whitespace. Safe for display too."""
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _BULLET.sub("", text)
    text = _HEADING.sub("", text)
    text = _EMPHASIS.sub("", text)
    text = _EMOJI.sub("", text)
    return _WS.sub(" ", text).strip()


def build_pronunciation_regex(pronunciations: dict[str, str]):
    if not pronunciations:
        return None
    keys = sorted(pronunciations, key=len, reverse=True)
    return re.compile(r"(?<!\w)(" + "|".join(re.escape(k) for k in keys) + r")(?!\w)")


def normalise(text: str, pronunciations: dict[str, str] | None = None) -> str:
    """clean_text + knowledge/pronunciations.json replacements (case-sensitive, word-bounded)."""
    text = clean_text(text)
    pron = pronunciations or {}
    rx = build_pronunciation_regex(pron)
    if rx:
        text = rx.sub(lambda m: pron[m.group(1)], text)
    return text


class TTSProvider(Protocol):
    voice_id: str

    async def synth(self, text: str) -> bytes | None:
        """Return mp3 bytes, or None on final failure (caller falls back to browser speech)."""
        ...


class PollyTTS:
    TIMEOUT_S = 5.0
    RETRIES = 2
    BACKOFF_S = 0.2

    def __init__(self, client, voice_id: str, engine: str, pronunciations: dict[str, str] | None = None):
        self._client = client
        self.voice_id = voice_id
        self._engine = engine
        self._pron = pronunciations or {}
        self._rx = build_pronunciation_regex(self._pron)

    def _prepare(self, text: str) -> str:
        text = clean_text(text)
        if self._rx:
            text = self._rx.sub(lambda m: self._pron[m.group(1)], text)
        return text

    def _call(self, text: str) -> bytes:
        resp = self._client.synthesize_speech(
            Text=text,
            VoiceId=self.voice_id,
            Engine=self._engine,
            OutputFormat="mp3",
            SampleRate="24000",
            TextType="text",
        )
        return resp["AudioStream"].read()

    async def synth(self, text: str) -> bytes | None:
        prepared = self._prepare(text)
        if not prepared:
            return None
        for attempt in range(self.RETRIES + 1):
            try:
                return await asyncio.wait_for(asyncio.to_thread(self._call, prepared), self.TIMEOUT_S)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("polly attempt %d failed: %s", attempt + 1, exc)
                if attempt < self.RETRIES:
                    await asyncio.sleep(self.BACKOFF_S)
        return None
