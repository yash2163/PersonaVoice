"""Synthesize the filler clips and the apology clip with the configured Polly voice.

Usage (from repo root, with AWS creds + POLLY_VOICE_ID set in .env):
    .venv/bin/python scripts/gen_fillers.py
Writes static/fillers/*.mp3 and static/fillers/fillers.json. Commit the results.
"""
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import boto3  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.persona import load_persona  # noqa: E402
from app.pipeline import SPOKEN_APOLOGY  # noqa: E402
from app.tts import PollyTTS  # noqa: E402

# No filler takes a stance: the answer can't know which one played.
FILLERS = [
    "Hmm, let me think about that.",
    "Okay, good one.",
    "Right, so...",
    "Oh, that's an interesting one.",
    "Let me see...",
    "Hmm, okay.",
    "Sure.",
    "Alright.",
]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30]


async def main() -> None:
    s = get_settings()
    if not s.polly_voice_id:
        sys.exit("POLLY_VOICE_ID is not set (try: aws polly describe-voices --language-code en-IN)")
    persona = load_persona(s.knowledge_dir, cache=False)
    client = boto3.session.Session(
        region_name=s.aws_region,
        **({"aws_access_key_id": s.aws_access_key_id, "aws_secret_access_key": s.aws_secret_access_key} if s.aws_access_key_id else {}),
    ).client("polly")
    tts = PollyTTS(client, s.polly_voice_id, s.polly_engine, persona.pronunciations)

    out_dir = s.static_dir / "fillers"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.mp3"):
        old.unlink()

    manifest = {"fillers": [], "apology": None}
    for i, text in enumerate(FILLERS):
        audio = await tts.synth(text)
        if not audio:
            sys.exit(f"Polly failed for: {text}")
        name = f"{i:02d}-{slug(text)}.mp3"
        (out_dir / name).write_bytes(audio)
        manifest["fillers"].append({"id": f"f{i}", "text": text, "url": f"/fillers/{name}"})
        print("wrote", name)

    audio = await tts.synth(SPOKEN_APOLOGY)
    if not audio:
        sys.exit("Polly failed for the apology clip")
    (out_dir / "apology.mp3").write_bytes(audio)
    manifest["apology"] = {"text": SPOKEN_APOLOGY, "url": "/fillers/apology.mp3"}
    (out_dir / "fillers.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"done: {len(FILLERS)} fillers + apology, voice={s.polly_voice_id} engine={s.polly_engine}")


if __name__ == "__main__":
    asyncio.run(main())
