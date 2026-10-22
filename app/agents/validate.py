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
    evidence_text: str | None = None,
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
            evidence_text=evidence_text,
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
    evidence_text: str | None,
) -> Critique:
    missing = _missing_document_field(payload)
    if missing:
        return Critique(True, missing, ["PLANNER"], "planner")
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
        text = str(line.get("text") or "")
        acceptance = line.get("acceptance") or []
        pieces = [text]
        if isinstance(acceptance, list):
            pieces.extend(str(item) for item in acceptance)
        # Field patterns such as "ci_status success" describe the future gate.
        # They are checked on the claim text. Acceptance commands are checked
        # with the rest of the prose so a cargo gate cannot ride on a yarn repo.
        contradiction = _row_contradiction(text, line.get("evidence_ids") or [], row)
        if contradiction:
            rejected.append(line_id)
            reasons.append(f"{line_id} {contradiction}")
        ungrounded = _ungrounded("\n".join(pieces), row, evidence_text)
        if ungrounded:
            rejected.append(line_id)
            reasons.append(f"{line_id} {ungrounded}")
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


_REQUIRED_DOCUMENT_FIELDS = ("repo_id", "window", "problem", "findings", "stages")

# Longer tokens first so "GPL" is not also reported inside an accepted "GPL-3.0".
_LICENCE_TOKENS = (
    "BSD-3-Clause",
    "BSD-2-Clause",
    "Apache-2.0",
    "GPL-3.0",
    "GPL-2.0",
    "MPL-2.0",
    "AGPL",
    "LGPL",
    "GPL",
    "MIT",
    "ISC",
)
_NO_TESTS = re.compile(r"\b(?:0 tests|no tests|without tests)\b", re.IGNORECASE)
_REWRITE = re.compile(r"\brewrite in ([A-Za-z0-9+#.]+)", re.IGNORECASE)
_COMMANDS = (
    ("cargo", re.compile(r"\bcargo\b", re.IGNORECASE)),
    ("go test", re.compile(r"\bgo test\b", re.IGNORECASE)),
    ("pytest", re.compile(r"\bpytest\b", re.IGNORECASE)),
    ("mvn", re.compile(r"\bmvn\b", re.IGNORECASE)),
    ("gradle", re.compile(r"\bgradle\b", re.IGNORECASE)),
    ("dotnet", re.compile(r"\bdotnet\b", re.IGNORECASE)),
    ("bundle exec", re.compile(r"\bbundle exec\b", re.IGNORECASE)),
    ("yarn", re.compile(r"\byarn\b", re.IGNORECASE)),
    ("pnpm", re.compile(r"\bpnpm\b", re.IGNORECASE)),
    ("npm", re.compile(r"\bnpm\b", re.IGNORECASE)),
    ("node", re.compile(r"\bnode\b", re.IGNORECASE)),
)


def _missing_document_field(payload: dict) -> str:
    """A draft that cannot be rendered is rejected. The run uses the deterministic plan."""

    if not isinstance(payload, dict):
        return "Planner draft is not an object."
    for key in _REQUIRED_DOCUMENT_FIELDS:
        if key not in payload or payload.get(key) in (None, ""):
            return f"Planner draft is missing {key}."
    return ""


def _tests_present(row: dict | None) -> bool | None:
    if row is None or "has_tests" not in row:
        return None
    value = row.get("has_tests")
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    if value is None or value == "":
        return None
    return bool(value)


def _ungrounded(prose: str, row: dict | None, evidence_text: str | None) -> str:
    """Factual sentences have to match the cited row and the evidence toolchain.

    evidence_text None skips the toolchain check so a caller that only has the
    row can still reject a licence or test contradiction. An empty string is a
    real evidence blob and does reject a command that is not in it.
    """

    reasons: list[str] = []
    if row is not None:
        reasons.extend(_row_prose_ungrounded(prose, row))
    if evidence_text is not None:
        blob = evidence_text.lower()
        for name, pattern in _COMMANDS:
            if pattern.search(prose) and name.lower() not in blob:
                reasons.append(f"acceptance command {name} is not in the cited evidence toolchain.")
                break
    return " ".join(reasons)


def _row_prose_ungrounded(prose: str, row: dict) -> list[str]:
    reasons: list[str] = []
    licence = str(row.get("licence_spdx") or "").strip()
    for token in _LICENCE_TOKENS:
        if not re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9.+])", prose):
            continue
        if licence and (
            token.casefold() == licence.casefold() or licence.casefold().startswith(token.casefold() + "-")
        ):
            continue
        reasons.append(f"names licence {token}, which is not the cited row licence {licence or 'empty'}.")
        break
    if _tests_present(row) is True and _NO_TESTS.search(prose):
        reasons.append("claims the repository has no tests, but has_tests is true.")
    language = str(row.get("primary_language") or "").strip()
    rewrite = _REWRITE.search(prose)
    if rewrite and rewrite.group(1).casefold() != language.casefold():
        reasons.append(
            f"claims a rewrite in {rewrite.group(1)}, which is not primary_language {language or 'empty'}."
        )
    return reasons


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
