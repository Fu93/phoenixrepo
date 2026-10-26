"""Console encoding for command-line entry points.

On Windows with a non-UTF-8 code page (for example cp950 on a Traditional Chinese locale)
Python writes stdout in that code page, so UTF-8 text from READMEs, quotes and model replies
turns into mojibake or raises UnicodeEncodeError. Entry points call ensure_utf8_stdio() first.
"""
from __future__ import annotations

import sys


def ensure_utf8_stdio() -> None:
    """Switch stdout and stderr to UTF-8 (replacing unencodable characters). Safe to call twice."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        encoding = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if encoding == "utf8":
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            continue


def load_env_file(path: "str | None" = None) -> None:
    """Copy .env values into os.environ without overriding what is already set.

    Settings (pydantic) reads .env by itself, but several knobs are read straight from
    os.environ: PHOENIX_LLM_*, PHOENIX_ALTERNATIVES_*, PHOENIX_NEMOTRON_*, EVIDENCE_SOURCE,
    ZETARIS_URL. Without this, a value written in .env was silently ignored by those.
    A variable already set in the process environment wins, as with Settings.
    """
    from pathlib import Path

    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - python-dotenv is a runtime dependency
        return
    target = Path(path) if path else Path.cwd() / ".env"
    if target.is_file():
        load_dotenv(target, override=False)


def prepare_cli() -> None:
    """Common start of every command-line entry point: UTF-8 stdio, then .env into os.environ."""
    ensure_utf8_stdio()
    load_env_file()
