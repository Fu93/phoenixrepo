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
