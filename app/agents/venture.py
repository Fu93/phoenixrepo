"""Venture scorecard and protagonist selection for candidate repositories.

Evaluates remaining CANDIDATE repositories after discovery and equivalence
filtering to decide which repository is worth reviving into a real product.
Never uses the candidate repository's own stars, forks, or issues as a
quality or demand signal. Demand is measured strictly outside the repository.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple

CRITERIA_NAMES = (
    "problem_severity",
    "demand_evidence",
    "differentiation",
    "feasibility",
    "productization_path",
    "moat",
)

CRITERIA_WEIGHTS: dict[str, int] = {
    "problem_severity": 20,
    "demand_evidence": 20,
    "differentiation": 20,
    "feasibility": 15,
    "productization_path": 15,
    "moat": 10,
}

CRITERIA_ALIASES: dict[str, str] = {
    "problem": "problem_severity",
    "problem_severity": "problem_severity",
    "demand": "demand_evidence",
    "demand_evidence": "demand_evidence",
    "differentiation": "differentiation",
    "feasibility": "feasibility",
    "productization": "productization_path",
    "productization_path": "productization_path",
    "moat": "moat",
}

SEVEN_POWERS = frozenset(
    {
        "scale economies",
        "network economies",
        "counter-positioning",
        "switching costs",
        "branding",
        "cornered resource",
        "process power",
    }
)

INELIGIBLE_VERDICTS = frozenset({"ALREADY_EXISTS", "NO_GO"})


def canonical_criterion_name(name: str) -> str | None:
    """Normalize a criterion name or alias to canonical form."""
    lowered = name.strip().lower()
    return CRITERIA_ALIASES.get(lowered)


@dataclass
class Criterion:
    """A single venture scorecard dimension scored 0 to 3 with evidence grounding."""

    score: int
    evidence_ids: list[str] = field(default_factory=list)
    rationale: str = ""
    status: str = "SUPPORTED"
    flagged: str = ""
    unsupported: bool = False
    original_score: int | None = None

    def clamp(self, evidence_set: Collection[str]) -> Criterion:
        """Clamp unsupported score to 0 against the supplied evidence set."""
        return clamp_criterion(self, evidence_set)


def clamp_criterion(criterion: Criterion, evidence_set: Collection[str]) -> Criterion:
    """Deterministic guard: clamp positive score with no valid evidence to 0.

    A score above 0 whose evidence_ids contain no id present in the supplied
    evidence set is clamped to 0 and flagged UNSUPPORTED. The LLM never has
    the last word.
    """
    valid_ids = [eid for eid in criterion.evidence_ids if eid in evidence_set]
    if criterion.score > 0 and not valid_ids:
        criterion.original_score = criterion.score
        criterion.score = 0
        criterion.status = "UNSUPPORTED"
        criterion.flagged = "UNSUPPORTED"
        criterion.unsupported = True
    return criterion


def calculate_weighted_total(criteria: dict[str, Criterion | int | float] | Scorecard) -> float:
    """Calculate the 0-100 weighted total across the six scorecard criteria."""
    if isinstance(criteria, Scorecard):
        criteria_dict = criteria.criteria
    else:
        criteria_dict = criteria

    total = 0.0
    for name, weight in CRITERIA_WEIGHTS.items():
        val = criteria_dict.get(name)
        if val is None:
            # Check aliases
            for alias, canonical in CRITERIA_ALIASES.items():
                if canonical == name and alias in criteria_dict:
                    val = criteria_dict[alias]
                    break
        if val is None:
            continue

        if isinstance(val, Criterion):
            raw_score = val.score
        else:
            try:
                raw_score = int(val)
            except (TypeError, ValueError):
                raw_score = 0
        score = max(0, min(3, raw_score))
        total += weight * (score / 3.0)
    return round(total, 2)


@dataclass
class Scorecard:
    """Complete venture review scorecard for a candidate repository."""

    repo_id: str
    verdict: str
    criteria: dict[str, Criterion] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        repo_id: str,
        verdict: str,
        criteria: dict[str, Criterion | dict[str, Any] | int] | None = None,
        metadata: dict[str, Any] | None = None,
        evidence_set: Collection[str] | None = None,
        **kwargs: Any,
    ) -> None:
        self.repo_id = str(repo_id).strip()
        self.verdict = str(verdict).strip().upper()
        self.metadata = dict(metadata or {})
        self.criteria = {}

        raw_criteria: dict[str, Any] = {}
        if criteria:
            raw_criteria.update(criteria)
        for k, v in kwargs.items():
            raw_criteria[k] = v

        for key, val in raw_criteria.items():
            canonical = canonical_criterion_name(key)
            if canonical is None:
                continue
            if isinstance(val, Criterion):
                self.criteria[canonical] = val
            elif isinstance(val, dict):
                self.criteria[canonical] = Criterion(
                    score=int(val.get("score", 0)),
                    evidence_ids=list(val.get("evidence_ids") or []),
                    rationale=str(val.get("rationale") or ""),
                    status=str(val.get("status") or "SUPPORTED"),
                    flagged=str(val.get("flagged") or ""),
                    unsupported=bool(val.get("unsupported", False)),
                    original_score=val.get("original_score"),
                )
            elif isinstance(val, (int, float)):
                self.criteria[canonical] = Criterion(score=int(val))

        if evidence_set is not None:
            self.clamp(evidence_set)

    def __getattr__(self, name: str) -> Any:
        canonical = canonical_criterion_name(name)
        if canonical and "criteria" in self.__dict__ and canonical in self.criteria:
            return self.criteria[canonical]
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    @property
    def total(self) -> float:
        return calculate_weighted_total(self.criteria)

    @property
    def weighted_total(self) -> float:
        return self.total

    @property
    def is_eligible(self) -> bool:
        """Repos with verdict ALREADY_EXISTS or NO_GO are ineligible."""
        return self.verdict == "CANDIDATE" and self.verdict not in INELIGIBLE_VERDICTS

    @property
    def feasibility_score(self) -> int:
        crit = self.criteria.get("feasibility")
        return crit.score if crit is not None else 0

    def clamp(self, evidence_set: Collection[str]) -> Scorecard:
        """Clamp all criteria against the supplied evidence set in place."""
        for crit in self.criteria.values():
            crit.clamp(evidence_set)
        return self


def is_eligible_candidate(scorecard_or_verdict: Scorecard | str) -> bool:
    """Return whether candidate is eligible for revival prioritization."""
    if isinstance(scorecard_or_verdict, Scorecard):
        verdict = scorecard_or_verdict.verdict
    else:
        verdict = str(scorecard_or_verdict).strip().upper()
    return verdict == "CANDIDATE" and verdict not in INELIGIBLE_VERDICTS


class ProtagonistResult(NamedTuple):
    """Selection result returned by select_protagonist()."""

    protagonist: Scorecard | None
    reason: str

    def __bool__(self) -> bool:
        return self.protagonist is not None


def select_protagonist(
    candidates: Sequence[Scorecard | dict[str, Any]],
    evidence_set: Collection[str] | None = None,
) -> ProtagonistResult:
    """Select the protagonist repository from a sequence of candidate scorecards.

    Highest total among eligible CANDIDATEs with feasibility >= 2.
    Ties broken by repo_id ascending.
    Returns None plus a reason if nobody qualifies.
    """
    if not candidates:
        return ProtagonistResult(None, "No candidates provided.")

    scorecards: list[Scorecard] = []
    for item in candidates:
        if isinstance(item, Scorecard):
            sc = item
        elif isinstance(item, dict):
            sc = Scorecard(
                repo_id=str(item.get("repo_id") or ""),
                verdict=str(item.get("verdict") or ""),
                criteria=item.get("criteria"),
                metadata=item.get("metadata"),
            )
        else:
            continue

        if evidence_set is not None:
            sc.clamp(evidence_set)
        scorecards.append(sc)

    # 1. Eligibility gate: must be CANDIDATE, not ALREADY_EXISTS or NO_GO
    eligible = [sc for sc in scorecards if sc.is_eligible]
    if not eligible:
        return ProtagonistResult(
            None,
            "No eligible candidates: all repositories have ineligible verdicts (ALREADY_EXISTS or NO_GO).",
        )

    # 2. Feasibility gate: feasibility >= 2
    viable = [sc for sc in eligible if sc.feasibility_score >= 2]
    if not viable:
        return ProtagonistResult(
            None,
            "No eligible candidates meet feasibility threshold >= 2.",
        )

    # 3. Highest total score, ties broken by repo_id ascending
    viable.sort(key=lambda sc: (-sc.total, sc.repo_id))
    winner = viable[0]
    return ProtagonistResult(
        winner,
        f"Selected protagonist {winner.repo_id} with highest weighted total {winner.total} (feasibility {winner.feasibility_score}).",
    )


@dataclass
class ReworkRequest:
    """Request for scorecard re-investigation generated during review."""

    criterion: str
    issue: str
    detail: str

    def __str__(self) -> str:
        return f"{self.criterion}: [{self.issue}] {self.detail}"

    def __contains__(self, item: str) -> bool:
        return item.lower() in str(self).lower()

    def __getitem__(self, key: str) -> str:
        if hasattr(self, key):
            return getattr(self, key)
        raise KeyError(key)


def review_scorecard(
    scorecard: Scorecard | dict[str, Any],
    evidence_set: Collection[str] | None = None,
) -> list[ReworkRequest]:
    """Review a completed scorecard and return rework requests for re-investigation.

    Checks:
    - Missing criteria (must contain all six required criteria)
    - Missing evidence (score > 0 without evidence_ids or citing unknown IDs)
    - Score not matching its anchor (score out of bounds, demand citing repo stars,
      moat >= 2 without 7 Powers named, ALREADY_EXISTS claiming differentiation >= 2)
    """
    if isinstance(scorecard, dict):
        card = Scorecard(
            repo_id=str(scorecard.get("repo_id") or ""),
            verdict=str(scorecard.get("verdict") or ""),
            criteria=scorecard.get("criteria"),
            metadata=scorecard.get("metadata"),
        )
    else:
        card = scorecard

    rework: list[ReworkRequest] = []

    # 1. Missing criteria check
    for req_name in CRITERIA_NAMES:
        if req_name not in card.criteria:
            rework.append(
                ReworkRequest(
                    criterion=req_name,
                    issue="missing_criterion",
                    detail=f"Required criterion '{req_name}' is missing from the scorecard.",
                )
            )

    # 2. Per-criterion audits
    for name, crit in card.criteria.items():
        # Score out of bounds check
        if crit.score < 0 or crit.score > 3:
            rework.append(
                ReworkRequest(
                    criterion=name,
                    issue="score_anchor_mismatch",
                    detail=f"Criterion '{name}' score {crit.score} is outside the valid anchor range [0, 3].",
                )
            )

        # Clamped unsupported check
        if crit.status == "UNSUPPORTED" or crit.flagged == "UNSUPPORTED" or crit.unsupported:
            orig = crit.original_score if crit.original_score is not None else "claimed"
            rework.append(
                ReworkRequest(
                    criterion=name,
                    issue="missing_evidence",
                    detail=f"Criterion '{name}' claimed score {orig} was clamped to 0 because cited evidence_ids {crit.evidence_ids} are missing from the evidence set.",
                )
            )

        # Positive score with empty evidence_ids
        if crit.score > 0 and not crit.evidence_ids:
            rework.append(
                ReworkRequest(
                    criterion=name,
                    issue="missing_evidence",
                    detail=f"Criterion '{name}' scored {crit.score} > 0 but cited no evidence_ids.",
                )
            )

        # Check against supplied evidence set
        if evidence_set is not None:
            if crit.score > 0:
                missing_ids = [eid for eid in crit.evidence_ids if eid not in evidence_set]
                if missing_ids:
                    rework.append(
                        ReworkRequest(
                            criterion=name,
                            issue="missing_evidence",
                            detail=f"Criterion '{name}' cites evidence_ids not present in evidence set: {missing_ids}.",
                        )
                    )

        # Rationale presence check
        if not crit.rationale.strip() and crit.score > 0:
            rework.append(
                ReworkRequest(
                    criterion=name,
                    issue="score_anchor_mismatch",
                    detail=f"Criterion '{name}' has score {crit.score} without an explanatory rationale.",
                )
            )

        # Specific anchor alignment checks
        rationale_lower = crit.rationale.lower()

        # Demand evidence anchor: NEVER candidate repo's own stars/forks
        if name == "demand_evidence" and crit.score > 0:
            forbidden_stars = (
                "repo's stars",
                "repo stars",
                "repository stars",
                "candidate stars",
                "own stars",
                "stars count",
            )
            if any(term in rationale_lower for term in forbidden_stars) or (
                "stars" in rationale_lower and "external" not in rationale_lower and "related" not in rationale_lower
            ):
                rework.append(
                    ReworkRequest(
                        criterion="demand_evidence",
                        issue="score_anchor_mismatch",
                        detail="Demand evidence must be measured OUTSIDE the candidate repo. Candidate repo's own stars or forks cannot be used.",
                    )
                )

        # Moat anchor: score >= 2 requires naming at least one of the 7 Powers
        if name == "moat" and crit.score >= 2:
            if not any(power in rationale_lower for power in SEVEN_POWERS):
                rework.append(
                    ReworkRequest(
                        criterion="moat",
                        issue="score_anchor_mismatch",
                        detail=(
                            f"Moat score {crit.score} >= 2 requires naming a specific power from the 7 Powers "
                            f"(scale economies, network economies, counter-positioning, switching costs, "
                            f"branding, cornered resource, process power)."
                        ),
                    )
                )

        # Differentiation anchor: candidate with ALREADY_EXISTS cannot score >= 2
        if name == "differentiation" and card.verdict == "ALREADY_EXISTS" and crit.score >= 2:
            rework.append(
                ReworkRequest(
                    criterion="differentiation",
                    issue="score_anchor_mismatch",
                    detail="A candidate with verdict ALREADY_EXISTS cannot score differentiation >= 2.",
                )
            )

    return rework
