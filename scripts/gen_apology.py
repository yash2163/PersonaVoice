"""Synthesize the one pre-recorded clip the app ships: the apology played when a turn fails outright.

Usage (from repo root, with AWS creds + POLLY_VOICE_ID set in .env):
    .venv/bin/python scripts/gen_apology.py
Writes static/audio/apology.mp3. Commit the result. Re-run after changing the Polly voice or pacing.
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import boto3  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.persona import load_persona  # noqa: E402
from app.pipeline import SPOKEN_APOLOGY  # noqa: E402
from app.tts import PollyTTS  # noqa: E402


async def main() -> None:
    s = get_settings()
    if not s.polly_voice_id:
        sys.exit("POLLY_VOICE_ID is not set")
    persona = load_persona(s.knowledge_dir, cache=False)
    client = boto3.session.Session(
        region_name=s.polly_region or s.aws_region,
        **({"aws_access_key_id": s.aws_access_key_id, "aws_secret_access_key": s.aws_secret_access_key} if s.aws_access_key_id else {}),
    ).client("polly")
    tts = PollyTTS(
        client, s.polly_voice_id, s.polly_engine, persona.pronunciations,
        rate_pct=s.tts_rate_pct, comma_pause_ms=s.tts_comma_pause_ms,
    )
    audio = await tts.synth(SPOKEN_APOLOGY)
    if not audio:
        sys.exit("Polly failed for the apology clip")
    out = s.static_dir / "audio"
    out.mkdir(parents=True, exist_ok=True)
    (out / "apology.mp3").write_bytes(audio)
    print(f"wrote {out / 'apology.mp3'} ({len(audio)} bytes), voice={s.polly_voice_id} engine={s.polly_engine}")


asyncio.run(main())
