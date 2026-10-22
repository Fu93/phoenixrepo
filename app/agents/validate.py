"""Role authority for the critique loop. Unsupported claims are rejected here."""

from __future__ import annotations

import re
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


def critique_planner(
    payload: dict,
    *,
    known_evidence_ids: set[str],
    row: dict | None = None,
    evidence_by_id: dict | None = None,
) -> Critique:
    """Reject a PRD line that does not cite evidence, or that contradicts the cited row.

    A missing field is a rejection. It is not a KeyError for the run.
    """

    try:
        return _critique_planner(
            payload,
            known_evidence_ids=known_evidence_ids,
            row=row,
            evidence_by_id=evidence_by_id,
        )
    except (KeyError, TypeError, AttributeError) as exc:
        return Critique(
            True,
            f"Planner draft rejected because a field was missing or the wrong type: {type(exc).__name__}: {exc}",
            ["PLANNER"],
            "planner",
        )


def _critique_planner(
    payload: dict,
    *,
    known_evidence_ids: set[str],
    row: dict | None,
    evidence_by_id: dict | None,
) -> Critique:
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
            continue
        mismatch = _citation_mismatch(line, evidence_by_id)
        if mismatch:
            rejected.append(line_id)
            reasons.append(f"{line_id} {mismatch}")
        contradiction = _row_contradiction(str(line.get("text") or ""), line.get("evidence_ids") or [], row)
        if contradiction:
            rejected.append(line_id)
            reasons.append(f"{line_id} {contradiction}")
    if rejected:
        return Critique(True, " ".join(reasons), sorted(set(rejected)), "planner")
    return Critique(False)


_FIELD_PATTERNS = (
    ("licence_spdx", re.compile(r"licence\s+(\S+)", re.IGNORECASE)),
    ("has_tests", re.compile(r"has_tests\s+(true|false)", re.IGNORECASE)),
    ("has_license_file", re.compile(r"has_license_file\s+(true|false)", re.IGNORECASE)),
    ("ci_status", re.compile(r"ci_status\s+(\S+)", re.IGNORECASE)),
    ("primary_language", re.compile(r"primary_language\s+(\S+)", re.IGNORECASE)),
)


def _profile_id(evidence_ids: list) -> bool:
    return any(str(item).startswith("EV-PROFILE-") for item in evidence_ids)


def _same_value(column: str, claimed: str, actual) -> bool:
    if column in {"has_tests", "has_license_file"}:
        if isinstance(actual, str):
            actual_bool = actual.strip().lower() in {"true", "1", "yes"}
        else:
            actual_bool = bool(actual)
        return claimed.lower() == ("true" if actual_bool else "false")
    expected = str(actual if actual not in (None, "") else "empty").strip()
    if column == "ci_status" and expected == "":
        expected = "empty"
    if column == "licence_spdx" and expected == "":
        expected = "missing"
    return claimed.strip().rstrip(".,") == expected


def _row_contradiction(text: str, evidence_ids: list, row: dict | None) -> str:
    if row is None or not _profile_id(evidence_ids):
        return ""
    for field, pattern in _FIELD_PATTERNS:
        for match in pattern.finditer(text):
            if not _same_value(field, match.group(1), row.get(field)):
                return (
                    f"contradicts the cited row: {field} is {row.get(field)!r}, "
                    f"the draft says {match.group(1)}."
                )
    return ""


def _citation_mismatch(line: dict, evidence_by_id: dict | None) -> str:
    if not evidence_by_id:
        return ""
    citations = line.get("citations")
    if not citations:
        citations = [
            {
                "evidence_ids": line.get("evidence_ids") or [],
                "query_id": line.get("query_id"),
                "sql": line.get("sql"),
                "content_hash": line.get("content_hash") or "",
            }
        ]
    for citation in citations:
        if not isinstance(citation, dict):
            return "has a citation that is not an object."
        for evidence_id in citation.get("evidence_ids") or []:
            record = evidence_by_id.get(evidence_id)
            if not record:
                continue
            if citation.get("query_id") != record.get("query_id") or citation.get("sql") != record.get("sql"):
                return "query_id or sql does not match the evidence store."
            expected_hash = record.get("content_hash") or ""
            if expected_hash and citation.get("content_hash") != expected_hash:
                return "content hash does not match the evidence store."
    return ""


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
