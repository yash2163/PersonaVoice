"""FastAPI app: routes, static mount, startup warm-up."""
import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import boto3
import httpx
from botocore.config import Config as BotoConfig
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from .config import Settings, get_settings
from .llm import BedrockLLM
from .logging_conf import hash_ip, log_fields, setup_logging
from .persona import Persona, load_persona
from .pipeline import Pipeline
from .ratelimit import build_limiter, rate_limit_handler
from .schemas import AskRequest, HealthResponse, TranscribeResponse
from .stt import GroqWhisperSTT, NoSpeechError, STTUnavailable
from .tts import PollyTTS

logger = logging.getLogger(__name__)

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
ALLOWED_EXT = {"webm", "mp4", "m4a", "ogg", "wav", "mp3"}
FALLBACK_QUESTIONS = [
    "What should we know about your life story?",
    "What's your number one superpower?",
    "What are the top three areas you'd like to grow in?",
    "What misconception do your coworkers have about you?",
    "How do you push your boundaries and limits?",
]


def sse(event: dict) -> str:
    return f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"


def _filler_manifest(settings: Settings) -> dict:
    path = settings.static_dir / "fillers" / "fillers.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"fillers": [], "apology": None}


def create_app(
    settings: Settings | None = None,
    *,
    persona: Persona | None = None,
    llm=None,
    fallback_llm=None,
    tts=None,
    stt=None,
) -> FastAPI:
    """Providers can be injected (tests); anything not injected is built from settings at startup."""
    settings = settings or get_settings()
    limiter = build_limiter()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_logging(settings.log_level)
        loop = asyncio.get_running_loop()
        # Polly calls and boto3 streams share the default executor; the stock size (~5 on a
        # 1-vCPU box) would starve under a few concurrent testers.
        loop.set_default_executor(ThreadPoolExecutor(max_workers=32))

        p = persona or load_persona(settings.knowledge_dir, cache=settings.enable_prompt_cache)  # fails fast
        http_client = httpx.AsyncClient(limits=httpx.Limits(max_keepalive_connections=10))

        boto_kwargs = {"region_name": settings.aws_region}
        if settings.aws_access_key_id and settings.aws_secret_access_key:
            boto_kwargs.update(
                aws_access_key_id=settings.aws_access_key_id,
                aws_secret_access_key=settings.aws_secret_access_key,
            )
        boto_cfg = BotoConfig(
            max_pool_connections=20,
            connect_timeout=3,
            read_timeout=30,
            retries={"mode": "adaptive", "max_attempts": 3},
        )
        needs_boto = llm is None or tts is None
        runtime = polly = None
        if needs_boto:
            session = boto3.session.Session(**boto_kwargs)
            runtime = session.client("bedrock-runtime", config=boto_cfg)
            polly = session.client("polly", config=boto_cfg)

        the_llm = llm or BedrockLLM(
            runtime,
            settings.bedrock_model_id,
            settings.llm_temperature,
            settings.llm_max_tokens,
            use_cache=settings.enable_prompt_cache,
        )
        the_fallback = fallback_llm
        if the_fallback is None and runtime is not None and settings.bedrock_fallback_model_id:
            the_fallback = BedrockLLM(
                runtime,
                settings.bedrock_fallback_model_id,
                settings.llm_temperature,
                settings.llm_max_tokens,
                use_cache=False,  # Nova caching rules differ; keep the fallback path simple
            )
        the_tts = tts or PollyTTS(polly, settings.polly_voice_id, settings.polly_engine, p.pronunciations)
        the_stt = stt or GroqWhisperSTT(http_client, settings.groq_api_key, settings.stt_model, settings.stt_prompt)

        if not settings.bedrock_model_id and llm is None:
            logger.warning("BEDROCK_MODEL_ID is not set; /api/ask will fail until it is")

        app.state.persona = p
        app.state.system_blocks = p.system_blocks()
        app.state.stt = the_stt
        app.state.tts = the_tts
        app.state.pipeline = Pipeline(
            llm=the_llm,
            fallback_llm=the_fallback,
            tts=the_tts,
            tts_concurrency=settings.tts_concurrency,
            retry_delay_s=settings.llm_retry_delay_s,
            first_token_timeout_s=settings.llm_first_token_timeout_s,
            max_answer_words=settings.max_answer_words,
        )
        # Warm Polly's TLS connection so the first real sentence isn't slow.
        try:
            await the_tts.synth("Hello.")
        except Exception as exc:
            logger.warning("tts warm-up failed: %r", exc)
        yield
        await http_client.aclose()

    app = FastAPI(title="Yash voice bot", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

    # ----------------------------------------------------------------- routes
    @app.get("/api/health", response_model=HealthResponse)
    async def health(request: Request):
        pl: Pipeline = request.app.state.pipeline
        return HealthResponse(ok=True, llm=pl.llm.model_id, tts=request.app.state.tts.voice_id)

    @app.get("/api/config")
    async def config(request: Request):
        manifest = _filler_manifest(settings)
        return {
            "sample_questions": request.app.state.persona.sample_questions or FALLBACK_QUESTIONS,
            "fillers": manifest.get("fillers", []),
            "apology": manifest.get("apology"),
            "filler_threshold_ms": settings.filler_threshold_ms,
            "max_recording_s": settings.max_audio_seconds,
            "features": {"contextual_filler": settings.contextual_filler},
        }

    @app.post("/api/transcribe", response_model=TranscribeResponse)
    @limiter.limit(lambda: settings.rate_limit_stt)
    async def transcribe(
        request: Request,
        audio: UploadFile = File(...),
        duration_ms: int | None = Form(default=None),
    ):
        data = await audio.read(settings.max_audio_bytes + 1)
        if len(data) > settings.max_audio_bytes:
            raise HTTPException(413, "Audio too large")
        if duration_ms is not None and duration_ms > (settings.max_audio_seconds + 1) * 1000:
            raise HTTPException(413, "Audio too long")
        ext = (audio.filename or "").rsplit(".", 1)[-1].lower()
        if ext not in ALLOWED_EXT:
            ext = "mp4" if "mp4" in (audio.content_type or "") else "webm"
        t0 = time.perf_counter()
        try:
            text = await request.app.state.stt.transcribe(data, ext, duration_ms)
        except NoSpeechError:
            raise HTTPException(422, "Couldn't hear anything. Try again or type your question.")
        except STTUnavailable as exc:
            log_fields(logger, "stt failed", logging.WARNING, error=str(exc))
            raise HTTPException(502, "Couldn't hear that clearly. Try again or type your question.")
        ms = int((time.perf_counter() - t0) * 1000)
        log_fields(logger, "stt", stt_ms=ms, chars=len(text), bytes=len(data))
        return TranscribeResponse(text=text, ms=ms)

    @app.post("/api/ask")
    @limiter.limit(lambda: settings.rate_limit_ask)
    async def ask(request: Request, body: AskRequest):
        client_ip = request.client.host if request.client else "unknown"
        pl: Pipeline = request.app.state.pipeline
        events = pl.run(
            body.question,
            [m.model_dump() for m in body.history],
            request.app.state.system_blocks,
            ip_hash=hash_ip(client_ip, settings.ip_hash_salt),
            stt_ms=body.stt_ms,
            is_disconnected=request.is_disconnected,
        )

        async def stream():
            try:
                async for ev in events:
                    yield sse(ev)
            finally:
                await events.aclose()

        return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": "http_error", "message": exc.detail})

    # Same-origin static frontend, mounted last so /api/* wins.
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")
    return app


app = create_app()
