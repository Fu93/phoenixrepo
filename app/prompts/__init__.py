"""Versioned role prompts. The trace stores sha256 of the file bytes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

ROLES = (
    "archaeologist",
    "market_judge",
    "architect",
    "builder",
    "diagnostician",
    "planner",
    "reviewer",
    "equivalence",
    "venture_analyst",
    "venture_reviewer",
)

_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Prompt:
    role: str
    text: str
    sha256: str
    path: str


def load_prompt(role: str) -> Prompt:
    path = _DIR / f"{role}.md"
    data = path.read_bytes()
    return Prompt(role=role, text=data.decode("utf-8"), sha256=hashlib.sha256(data).hexdigest(), path=str(path))


def prompt_versions() -> dict[str, str]:
    return {role: load_prompt(role).sha256 for role in ROLES}
