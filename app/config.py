"""All configuration, read from environment variables (and an optional .env file)."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # AWS
    aws_region: str = "ap-south-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""

    # LLM (Bedrock)
    bedrock_model_id: str = ""
    bedrock_fallback_model_id: str = ""
    enable_prompt_cache: bool = True
    llm_temperature: float = 0.6
    llm_max_tokens: int = 350
    llm_first_token_timeout_s: float = 3.5
    llm_retry_delay_s: float = 0.3
    # Safety net: stop at the next sentence boundary once an answer passes this many words.
    max_answer_words: int = 120

    # TTS (Polly)
    polly_voice_id: str = ""
    polly_engine: str = "neural"  # or "generative"
    tts_concurrency: int = 3
    # Delivery: neural voices are fast and run sentences together, so slow down a touch and pause.
    tts_rate_pct: int = 86
    tts_comma_pause_ms: int = 120
    tts_sentence_pause_ms: int = 420

    # STT (Groq)
    groq_api_key: str = ""
    stt_model: str = "whisper-large-v3-turbo"
    stt_prompt: str = "Yash Rajput, Minfy, VIT Vellore, LangGraph, Bedrock, RAG, LoRA, QLoRA, vLLM, OpenSearch, 100x"
    max_audio_bytes: int = 3 * 1024 * 1024
    max_audio_seconds: int = 30

    # Client behaviour / limits
    filler_threshold_ms: int = 800
    contextual_filler: bool = False
    rate_limit_ask: str = "20/minute;300/day"
    rate_limit_stt: str = "20/minute"

    log_level: str = "INFO"
    ip_hash_salt: str = "yash-voicebot"

    # Paths
    knowledge_dir: Path = ROOT / "knowledge"
    static_dir: Path = ROOT / "static"


@lru_cache
def get_settings() -> Settings:
    return Settings()
