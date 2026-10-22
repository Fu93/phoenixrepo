"""Already-exists detection for a Zetaris DDL response.

The live install returns HTTP 400 with {"msg": "A database named \\"PHOENIX_REAL\\" already exists."}
The same phrase can sit in error, error.message, or the raw body. Any of those counts.
A body that does not contain the phrase is still a failure, including another HTTP 400.
"""

from __future__ import annotations

import json
import re
from typing import Any

_ALREADY = re.compile(r"already exists|already exist", re.IGNORECASE)


def _strings(value: Any, found: list[str]) -> None:
    if isinstance(value, str):
        found.append(value)
        return
    if isinstance(value, dict):
        for item in value.values():
            _strings(item, found)
        return
    if isinstance(value, list):
        for item in value:
            _strings(item, found)


def already_exists_text(content: str) -> str:
    """Text searched for the already-exists phrase. Includes the raw body and every string field."""

    if content is None or not str(content).strip():
        return ""
    raw = str(content)
    found = [raw]
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    if parsed is not None:
        _strings(parsed, found)
    return "\n".join(found)


def counts_as_exists(content: str, status_code: int | None = None) -> bool:
    """True when the body says the object already exists. The HTTP status does not matter."""

    del status_code
    return _ALREADY.search(already_exists_text(content)) is not None
