"""Role authority for the critique loop. Unsupported claims are rejected here."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.agents.policy import decide


@dataclass
class Critique:
    rejected: bool
    reason: str = ""
    claim_ids: list[str] = field(default_factory=list)
    target_agent: str = ""


def critique_archaeologist(payload: dict, *, known_evidence_ids: set[str], row: dict) -> Critique:
    claims = payload.get("claims")
    if not isinstance(claims, list) or not claims:
        return Critique(True, "Archaeologist returned no claims.", [], "archaeologist")
    rejected: list[str] = []
    reasons: list[str] = []
    blob = " ".join(str(claim.get("statement", "")) for claim in claims if isinstance(claim, dict))
    for claim in claims:
        if not isinstance(claim, dict):
            return Critique(True, "A claim was not an object.", [], "archaeologist")
        evidence_ids = claim.get("evidence_ids") or []
        claim_id = str(claim.get("id") or "unknown")
        if not evidence_ids:
            rejected.append(claim_id)
            reasons.append(f"{claim_id} has no evidence id.")
            continue
        unknown = [item for item in evidence_ids if item not in known_evidence_ids]
        if unknown:
            rejected.append(claim_id)
            reasons.append(f"{claim_id} cites unknown evidence {unknown}.")
    required = [str(row["licence_spdx"]), str(row["days_since_push"]), str(row["similar_repo_count"])]
    missing = [token for token in required if token and token not in blob]
    if missing:
        reasons.append("Claims do not quote row values: " + ", ".join(missing))
        rejected.extend(claim.get("id", "claims") for claim in claims if isinstance(claim, dict))
    if rejected:
        return Critique(True, " ".join(reasons), sorted(set(map(str, rejected))), "archaeologist")
    return Critique(False)


def critique_judge(payload: dict, *, known_evidence_ids: set[str], row: dict, signals: list[dict]) -> Critique:
    expected, reason = decide(row, signals)
    got = str(payload.get("decision") or "")
    evidence_ids = payload.get("evidence_ids") or []
    explanation = str(payload.get("explanation") or "")
    problems: list[str] = []
    if got != expected:
        problems.append(f"Judge said {got or 'nothing'}; the cited rows support {expected} because {reason}.")
    if not evidence_ids or any(item not in known_evidence_ids for item in evidence_ids):
        problems.append("Judge cited evidence that is not in the Zetaris result.")
    if str(row["licence_spdx"]) not in explanation or str(row["days_since_push"]) not in explanation:
        problems.append("Judge explanation does not quote the licence and days_since_push from the cited row.")
    if problems:
        return Critique(True, " ".join(problems), ["CLM-DECISION"], "market_judge")
    return Critique(False)


def critique_planner(payload: dict, *, known_evidence_ids: set[str]) -> Critique:
    """Reject a PRD line that does not cite evidence already in the investigation."""

    findings = payload.get("findings")
    stages = payload.get("stages")
    if not isinstance(findings, list) or not findings:
        return Critique(True, "Planner returned no findings.", ["PLANNER"], "planner")
    if not isinstance(stages, list) or [stage.get("id") for stage in stages if isinstance(stage, dict)] != [
        "revive",
        "refactor",
        "upgrade",
    ]:
        return Critique(True, "Planner stages must be revive, refactor, then upgrade.", ["PLANNER"], "planner")
    rejected: list[str] = []
    reasons: list[str] = []
    lines: list[tuple[str, dict]] = []
    for finding in findings:
        if isinstance(finding, dict):
            lines.append((str(finding.get("id") or "finding"), finding))
    for stage in stages:
        if not isinstance(stage, dict):
            return Critique(True, "A stage was not an object.", ["PLANNER"], "planner")
        tasks = stage.get("tasks") or []
        if not tasks:
            reasons.append(f"Stage {stage.get('id')} has no tasks.")
            rejected.append(str(stage.get("id") or "stage"))
        for task in tasks:
            if isinstance(task, dict):
                lines.append((str(task.get("id") or "task"), task))
                acceptance = task.get("acceptance") or []
                if not acceptance:
                    rejected.append(str(task.get("id") or "task"))
                    reasons.append(f"{task.get('id') or 'task'} has no acceptance criteria.")
    for risks in payload.get("risks") or []:
        if isinstance(risks, dict):
            lines.append((str(risks.get("id") or "risk"), risks))
    for line_id, line in lines:
        evidence_ids = line.get("evidence_ids") or []
        if not evidence_ids:
            rejected.append(line_id)
            reasons.append(f"{line_id} has no evidence id.")
            continue
        unknown = [item for item in evidence_ids if item not in known_evidence_ids]
        if unknown:
            rejected.append(line_id)
            reasons.append(f"{line_id} cites unknown evidence {unknown}.")
        if not line.get("query_id") or not line.get("sql"):
            rejected.append(line_id)
            reasons.append(f"{line_id} has no query id or sql.")
    if rejected:
        return Critique(True, " ".join(reasons), sorted(set(rejected)), "planner")
    return Critique(False)


def critique_builder(payload: dict, *, known_evidence_ids: set[str]) -> Critique:
    if str(payload.get("verification_result", "")).upper() == "PASS":
        return Critique(
            True,
            "Builder cannot write PASS. Validator is the only role that may accept a revival.",
            ["BUILDER"],
            "builder",
        )
    evidence_ids = payload.get("evidence_ids") or []
    if not evidence_ids or any(item not in known_evidence_ids for item in evidence_ids):
        return Critique(True, "Builder output has no resolved evidence ids.", ["BUILDER"], "builder")
    steps = payload.get("steps") or []
    if not steps:
        return Critique(True, "Builder returned no steps.", ["BUILDER"], "builder")
    return Critique(False)
