"""Loads the persona files and builds Bedrock system blocks."""
import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class PersonaError(RuntimeError):
    pass


@dataclass(frozen=True)
class Persona:
    system_prompt: str
    profile: str
    pronunciations: dict[str, str]
    sample_questions: list[str]
    cache: bool

    def system_blocks(self) -> list[dict]:
        blocks: list[dict] = [{"text": self.system_prompt}]
        if self.cache:
            blocks.append({"cachePoint": {"type": "default"}})
        return blocks


def load_persona(knowledge_dir: Path, cache: bool = True) -> Persona:
    def read(name: str) -> str:
        path = knowledge_dir / name
        if not path.is_file():
            raise PersonaError(f"missing knowledge file: {path}")
        return path.read_text(encoding="utf-8")

    profile = read("profile.md").strip()
    if not profile:
        raise PersonaError("knowledge/profile.md is empty; fill it in before starting")
    template = read("system_prompt.md").strip()
    if "{profile}" not in template:
        raise PersonaError("knowledge/system_prompt.md must contain a {profile} placeholder")
    if len(profile.split()) < 300:
        logger.warning("profile.md is short (%d words); answers will be thin", len(profile.split()))

    prompt = template.replace("{profile}", profile)  # str.replace: profile text may contain braces

    pron_path = knowledge_dir / "pronunciations.json"
    pronunciations = json.loads(pron_path.read_text(encoding="utf-8")) if pron_path.is_file() else {}
    sq_path = knowledge_dir / "sample_questions.json"
    samples = json.loads(sq_path.read_text(encoding="utf-8")) if sq_path.is_file() else []
    return Persona(prompt, profile, pronunciations, samples, cache)
