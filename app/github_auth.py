"""GitHub token handling shared by every GitHub caller.

An expired or revoked GITHUB_TOKEN makes GitHub answer HTTP 401 to every request, even for
public data that needs no token. Gate 1 then sees every search and every alternative's facts
as a source error, and every repository becomes NEEDS_REVIEW. The first 401 marks the token
bad for this process: callers retry once without it and stop sending it, and one warning says
so. Unauthenticated access is limited (60 core requests per hour, 10 searches per minute), so
the warning asks for a new token rather than hiding the problem.
"""
from __future__ import annotations

import sys
import threading

_lock = threading.Lock()
_bad_tokens: set[str] = set()


def token_usable(token: str | None) -> bool:
    """False for an empty token or one GitHub already rejected in this process."""
    return bool(token) and token not in _bad_tokens


def mark_unauthorized(token: str | None) -> bool:
    """Record a 401 for this token. True when this call is the one that marked it (retry once)."""
    if not token:
        return False
    with _lock:
        if token in _bad_tokens:
            return False
        _bad_tokens.add(token)
    sys.stderr.write(
        "warning: GitHub answered HTTP 401 to GITHUB_TOKEN (expired or revoked). Continuing without "
        "a token for the rest of this run; unauthenticated GitHub allows 60 requests per hour and 10 "
        "searches per minute, so searches may hit RATE_LIMITED. Create a new token and update .env.\n"
    )
    sys.stderr.flush()
    return True


def reset_for_tests() -> None:
    with _lock:
        _bad_tokens.clear()
