"""First gate for the revival flow: is the repository worth saving at all?

A repository can be technically revived and still be a reinvented wheel. Before the revival flow
may report RESURRECTED (or CANDIDATE), the same prior-art judgement the discovery run uses has to
agree: understand the problem, search GitHub, npm and the web, verify quotes. This module is the
only place the revival flow reaches that judgement, so it can be replaced in tests.
"""
from __future__ import annotations

from typing import Any, Callable

GateFn = Callable[[str, str], dict[str, Any]]

REVIVED_NOT_RECOMMENDED = "REVIVED_NOT_RECOMMENDED"
NEEDS_REVIEW = "NEEDS_REVIEW"
GATED_DECISIONS = frozenset({"RESURRECTED", "CANDIDATE"})


def judge_first_gate(repository: str, run_id: str) -> dict[str, Any]:
    """Run the discovery-style prior-art judgement for one repository. Raises on any failure."""
    from scripts import judge_repo  # imported lazily: it pulls in the batch scorer

    return judge_repo.judge(f"https://github.com/{repository}", use_llm=True, run_id=run_id)


def apply_first_gate(decision: str, reason: str, outcome: dict[str, Any] | None, error: str = "") -> tuple[str, str]:
    """Turn a gate outcome into the final decision. Only a clean CANDIDATE verdict lets the decision stand."""
    if decision not in GATED_DECISIONS:
        return decision, reason
    if error or not outcome:
        why = error or "no outcome was returned"
        return NEEDS_REVIEW, (
            f"{reason} First gate (prior art) could not complete: {why}. "
            "A technically successful revival is not accepted without a prior-art check."
        )
    verdict = str(outcome.get("verdict") or "")
    detail = str(outcome.get("reason") or "")[:600]
    ref = f" Evidence report {outcome.get('report')}." if outcome.get("report") else ""
    if verdict == "CANDIDATE":
        return decision, f"{reason} First gate passed: no mainstream or open-source substitute covers the core problem.{ref}"
    if verdict == "ALREADY_EXISTS":
        return REVIVED_NOT_RECOMMENDED, (
            f"Technically revived ({decision}) but not recommended: the first gate found the same problem already solved. "
            f"{detail}{ref}"
        )
    return NEEDS_REVIEW, (
        f"{reason} First gate verdict {verdict or 'missing'}, so a human has to decide. {detail}{ref}"
    )
