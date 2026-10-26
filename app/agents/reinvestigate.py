"""Automatic re-investigation loop using capped sub-agents."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents.subagents import SubAgentManager
from app.agents.venture import (
    Criterion,
    ReworkRequest,
    Scorecard,
    canonical_criterion_name,
    clamp_criterion,
    review_scorecard,
)
from app.tools.base import ToolBox

AnalystCallable = Callable[..., Criterion | dict[str, Any]]


@dataclass
class ReinvestigationResult:
    """Outcome of the automatic re-investigation loop."""

    scorecard: Scorecard
    evidence: list[dict[str, Any]]
    rounds_executed: int
    status: str  # "ok", "LLM_FALLBACK", "BUDGET_EXHAUSTED", "RATE_LIMITED"
    rework_requests: list[ReworkRequest] = field(default_factory=list)
    subagents_used: int = 0
    flags: list[str] = field(default_factory=list)

    def __iter__(self):
        """Allow unpacking as (scorecard, evidence)."""
        return iter((self.scorecard, self.evidence))


def reinvestigate(
    scorecard: Scorecard,
    evidence: list[dict[str, Any]],
    toolbox: ToolBox,
    analyst: AnalystCallable | None = None,
    max_rounds: int = 2,
    subagent_manager: SubAgentManager | None = None,
) -> ReinvestigationResult:
    """Run iterative scorecard review and sub-agent evidence gathering.

    For each rework request of issue missing_evidence or score_anchor_mismatch:
    - Spawn a capped sub-agent to gather evidence for that one criterion
    - Add new evidence items to the evidence pool
    - Re-score only that criterion with the analyst
    - Re-clamp deterministically: the deterministic guard always has the last word
    - Repeat up to max_rounds

    If budget runs out or rounds are exhausted, stop and keep the clamped scorecard,
    recording status LLM_FALLBACK or BUDGET_EXHAUSTED in the result.
    """
    evidence_list = list(evidence)
    evidence_set: set[str] = {str(item.get("id")) for item in evidence_list if item.get("id")}
    manager = subagent_manager or SubAgentManager()

    # Initial deterministic clamp
    scorecard.clamp(evidence_set)

    # Check initial budget
    if toolbox.budget.is_exhausted():
        scorecard.metadata["status"] = "BUDGET_EXHAUSTED"
        unsupported = any(c.status == "UNSUPPORTED" for c in scorecard.criteria.values())
        flags = ["BUDGET_EXHAUSTED"]
        if unsupported:
            flags.append("UNSUPPORTED")
        return ReinvestigationResult(
            scorecard=scorecard,
            evidence=evidence_list,
            rounds_executed=0,
            status="BUDGET_EXHAUSTED",
            rework_requests=review_scorecard(scorecard, evidence_set=evidence_set),
            subagents_used=manager.spawned_count,
            flags=flags,
        )

    status = "ok"
    rounds_executed = 0
    current_rework: list[ReworkRequest] = []

    for round_idx in range(1, max_rounds + 1):
        rounds_executed = round_idx
        current_rework = review_scorecard(scorecard, evidence_set=evidence_set)

        actionable = [
            req
            for req in current_rework
            if req.issue in {"missing_evidence", "score_anchor_mismatch"}
        ]
        if not actionable:
            # All evidence and anchor issues are resolved
            break

        round_improved = False
        for req in actionable:
            if toolbox.budget.is_exhausted():
                status = "BUDGET_EXHAUSTED"
                break

            if not manager.can_spawn():
                status = "LLM_FALLBACK"
                break

            canonical = canonical_criterion_name(req.criterion) or req.criterion
            try:
                subagent = manager.spawn(criterion=canonical, repo_id=scorecard.repo_id, toolbox=toolbox)
            except RuntimeError:
                status = "LLM_FALLBACK"
                break

            # Let subagent gather new evidence
            alts = scorecard.metadata.get("alternative_repos")
            new_items = subagent.investigate(query=scorecard.repo_id, alternative_repos=alts)

            if new_items:
                for item in new_items:
                    eid = str(item.get("id") or "")
                    if eid and eid not in evidence_set:
                        evidence_list.append(item)
                        evidence_set.add(eid)
                round_improved = True

            # Re-score only this criterion with the analyst
            if analyst is not None:
                try:
                    new_val = analyst(
                        criterion=canonical,
                        repo_id=scorecard.repo_id,
                        evidence=evidence_list,
                        scorecard=scorecard,
                    )
                except TypeError:
                    # In case analyst only takes (criterion, repo_id, evidence)
                    try:
                        new_val = analyst(canonical, scorecard.repo_id, evidence_list)
                    except TypeError:
                        new_val = analyst(canonical, scorecard.repo_id)

                if isinstance(new_val, Criterion):
                    crit = new_val
                elif isinstance(new_val, dict):
                    crit = Criterion(
                        score=int(new_val.get("score", 0)),
                        evidence_ids=list(new_val.get("evidence_ids") or []),
                        rationale=str(new_val.get("rationale") or ""),
                    )
                else:
                    crit = Criterion(score=int(new_val))

                # Deterministic clamping: LLM never has the last word
                clamp_criterion(crit, evidence_set)
                scorecard.criteria[canonical] = crit

            if getattr(subagent, "rate_limited", False):
                status = "RATE_LIMITED"
                break

        if status in {"BUDGET_EXHAUSTED", "LLM_FALLBACK", "RATE_LIMITED"}:
            break

        # Re-clamp the entire scorecard against the updated evidence set
        scorecard.clamp(evidence_set)

        if not round_improved and len(actionable) == len(review_scorecard(scorecard, evidence_set=evidence_set)):
            # No progress made in this round
            break

    # Final review and clamping
    scorecard.clamp(evidence_set)
    final_rework = review_scorecard(scorecard, evidence_set=evidence_set)

    if status == "ok":
        remaining_actionable = [
            r for r in final_rework if r.issue in {"missing_evidence", "score_anchor_mismatch"}
        ]
        if remaining_actionable:
            status = "LLM_FALLBACK"

    scorecard.metadata["status"] = status
    flags: list[str] = []
    if any(c.status == "UNSUPPORTED" for c in scorecard.criteria.values()):
        flags.append("UNSUPPORTED")
    if status in {"BUDGET_EXHAUSTED", "LLM_FALLBACK", "RATE_LIMITED"}:
        flags.append(status)
    if any(getattr(sub, "rate_limited", False) for sub in manager.subagents):
        if "RATE_LIMITED" not in flags:
            flags.append("RATE_LIMITED")
    if any(getattr(sub, "budget_exhausted", False) for sub in manager.subagents):
        if "BUDGET_EXHAUSTED" not in flags:
            flags.append("BUDGET_EXHAUSTED")

    return ReinvestigationResult(
        scorecard=scorecard,
        evidence=evidence_list,
        rounds_executed=rounds_executed,
        status=status,
        rework_requests=final_rework,
        subagents_used=manager.spawned_count,
        flags=flags,
    )
