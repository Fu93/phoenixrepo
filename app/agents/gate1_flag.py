"""Default-off switch for the Gate 1 evidence data-flow repair.

``PHOENIX_GATE1_EVIDENCE_REPAIR`` is read only when set to ``1``, ``true``, ``yes``,
``on``, or ``enabled``. Unset, blank, and every other value leave the verdict logic,
the tool calls, the prompts, and therefore the cache keys exactly as they were on main.
It is independent of ``PHOENIX_STRICT_SUBSTITUTION`` and ``PHOENIX_UNKNOWN_BLOCKER_REVIEW``.
"""
from __future__ import annotations

import os

FLAG = "PHOENIX_GATE1_EVIDENCE_REPAIR"
RULE_B_FLAG = "PHOENIX_UNKNOWN_BLOCKER_REVIEW"
_ON = frozenset({"1", "true", "yes", "on", "enabled"})


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().casefold() in _ON


def enabled() -> bool:
    """True only when the repair switch is explicitly on."""
    return _truthy(FLAG)


def trace_passthrough_enabled() -> bool:
    """Hand the run trace to ``judge_discovery`` only when a flagged feature records into it.

    Default runs keep logging through the client's own trace, as on main.
    """
    return evidence_scope_enabled() or _truthy("PHOENIX_STRICT_SUBSTITUTION")


def evidence_scope_enabled() -> bool:
    """The repair, or Rule B (its strict ``is True`` fact reading is needed by Rule B)."""
    return _truthy(FLAG) or _truthy(RULE_B_FLAG)
