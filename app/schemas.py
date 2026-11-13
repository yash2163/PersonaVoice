"""Request/response models."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1500)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    history: list[ChatMessage] = Field(default_factory=list, max_length=6)
    # Optional, client-measured transcription time; only used for logging.
    stt_ms: int | None = Field(default=None, ge=0, le=600_000)

    @field_validator("question")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question is empty")
        return v

    @model_validator(mode="after")
    def _check_history(self) -> "AskRequest":
        # Bedrock needs strictly alternating roles starting with user, and the new
        # question is appended as a user turn, so history must end with assistant.
        for i, m in enumerate(self.history):
            expected = "user" if i % 2 == 0 else "assistant"
            if m.role != expected:
                raise ValueError("history must start with user and alternate roles")
        if self.history and self.history[-1].role != "assistant":
            raise ValueError("history must end with an assistant message")
        return self


class TranscribeResponse(BaseModel):
    text: str
    ms: int


class HealthResponse(BaseModel):
    ok: bool
    llm: str
    tts: str
