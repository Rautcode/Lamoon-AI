"""Prompt registry (platform §9). Prompts are versioned data in VCS, never
inline in business logic. Reproducibility = a hash of the full recipe.
"""
import hashlib
from dataclasses import dataclass
from pathlib import Path

_PROMPT_DIR = Path(__file__).parent / "prompts_store"


@dataclass(frozen=True)
class Recipe:
    prompt_key: str
    prompt_version: str
    output_schema_version: str
    model: str
    temperature: float

    def hash(self, prompt_text: str) -> str:
        raw = f"{prompt_text}|{self.output_schema_version}|{self.model}|{self.temperature}"
        return hashlib.sha256(raw.encode()).hexdigest()


def get(key: str, version: str) -> str:
    """Load prompt text for key@version. ponytail: files now; a DB table only
    if non-devs need to edit prompts without a deploy."""
    path = _PROMPT_DIR / key / f"{version}.txt"
    return path.read_text(encoding="utf-8")
