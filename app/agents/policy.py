"""Evidence policy for the terminal state.

The policy reads Zetaris rows. A model may phrase the explanation. It cannot
change a row. RESURRECTED is only a qualifying stage-1 revival success.
A row that passes the rules with no such success is CANDIDATE.
"""

from __future__ import annotations

from typing import Any

from app.errors import InvestigationError
from app.sql_catalog import (
    ABANDONED_AFTER_DAYS,
    ALTERNATIVES_LOW_AT_MOST,
    ALTERNATIVES_MODERATE_AT_MOST,
    PERMISSIVE_LICENCES,
)


_EMPTY_TEXT = {"", "none", "null", "nan"}


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def blank_text(value: Any) -> str:
    """Zetaris returns every cell as a string. Null markers are empty policy inputs."""

    text = str(value if value is not None else "").strip()
    if text.lower() in _EMPTY_TEXT:
        return ""
    return text


def normalize_profile(row: dict[str, Any]) -> dict[str, Any]:
    required = (
        "repo_id",
        "licence_spdx",
        "days_since_push",
        "similar_repo_count",
        "unique_capability",
        "successor_url",
        "has_tests",
        "has_license_file",
        "ci_status",
        "category",
        "description",
        "data_origin",
    )
    missing = [column for column in required if column not in row]
    if missing:
        raise InvestigationError(
            "ZetarisSchemaError",
            "phoenix.repo_evidence is missing columns: " + ", ".join(missing),
            status_code=502,
        )
    try:
        days = int(float(row["days_since_push"]))
    except (TypeError, ValueError) as exc:
        raise InvestigationError(
            "ZetarisSchemaError",
            "days_since_push must be a number.",
            status_code=502,
        ) from exc
    similar_text = blank_text(row.get("similar_repo_count"))
    if not similar_text:
        similar = None
    else:
        try:
            similar = int(float(similar_text))
        except (TypeError, ValueError) as exc:
            raise InvestigationError(
                "ZetarisSchemaError",
                "similar_repo_count must be a number or empty when the search total is missing.",
                status_code=502,
            ) from exc
    return {
        **row,
        "repo_id": blank_text(row["repo_id"]),
        "licence_spdx": blank_text(row.get("licence_spdx")),
        "days_since_push": days,
        "similar_repo_count": similar,
        "unique_capability": blank_text(row.get("unique_capability")),
        "successor_url": blank_text(row.get("successor_url")),
        "has_tests": as_bool(row.get("has_tests")),
        "has_license_file": as_bool(row.get("has_license_file")),
        "has_readme": as_bool(row.get("has_readme")),
        "has_ci": as_bool(row.get("has_ci")),
        "ci_status": blank_text(row.get("ci_status")).lower(),
        "category": blank_text(row.get("category")),
        "description": blank_text(row.get("description")),
        "data_origin": blank_text(row.get("data_origin")),
        "archived": as_bool(row.get("archived")),
    }


def normalize_signal(row: dict[str, Any]) -> dict[str, Any]:
    missing = [column for column in ("repo_id", "signal", "detail", "supports_revival") if column not in row]
    if missing:
        raise InvestigationError(
            "ZetarisSchemaError",
            "phoenix.revival_signals is missing columns: " + ", ".join(missing),
            status_code=502,
        )
    return {
        "repo_id": blank_text(row["repo_id"]),
        "signal": blank_text(row["signal"]),
        "detail": blank_text(row["detail"]),
        "supports_revival": as_bool(row["supports_revival"]),
    }


_CI_FAILURES = {"failure", "cancelled", "timed_out", "startup_failure"}


def ci_kind(status: str) -> str:
    if status == "success":
        return "success"
    if status in _CI_FAILURES:
        return "failure"
    return "unknown"


def alternatives_factor(count: int | None) -> str:
    """Scored note. The count never decides the verdict by itself."""

    uncertainty = (
        "Uncertainty: the count is a GitHub search total for a close name or description query, "
        "not a census of substitutes, and it does not by itself decide the verdict."
    )
    if count is None:
        return "alternatives_factor unknown because no search total was recorded. " + uncertainty
    if count <= ALTERNATIVES_LOW_AT_MOST:
        band = "low"
    elif count <= ALTERNATIVES_MODERATE_AT_MOST:
        band = "moderate"
    else:
        band = "high"
    return f"alternatives_factor {band} (close-substitute search total_count {count}). " + uncertainty


def blocking_value_reason(row: dict[str, Any]) -> str | None:
    if row["licence_spdx"] not in PERMISSIVE_LICENCES:
        return f"licence {row['licence_spdx'] or 'missing'} is not in the permissive set"
    if row["successor_url"]:
        return "a successor URL is recorded"
    if row["days_since_push"] < ABANDONED_AFTER_DAYS:
        return f"last push was {row['days_since_push']} days ago, under the {ABANDONED_AFTER_DAYS}-day abandonment bar"
    if not row["unique_capability"]:
        return "unique_capability is empty"
    return None


def validation_block_reason(row: dict[str, Any], signals: list[dict[str, Any]]) -> str | None:
    if not row["has_tests"]:
        return "has_tests is false"
    if not row["has_license_file"]:
        return "has_license_file is false"
    if ci_kind(row["ci_status"]) == "failure":
        for signal in signals:
            if signal["signal"] == "ci_failure" and "toolchain setup step" in signal["detail"].lower():
                return signal["detail"]
        return f"ci_status is {row['ci_status']}"
    for signal in signals:
        if signal["signal"] in {"tests_absent", "ci_failure"} and not signal["supports_revival"]:
            return f"signal {signal['signal']} says revival is not supported"
    return None


def normalize_attempt(row: dict[str, Any]) -> dict[str, Any]:
    required = (
        "repo_id",
        "fork_full_name",
        "stage",
        "status",
        "ci_status",
        "ci_run_url",
        "commit_sha",
    )
    missing = [column for column in required if column not in row]
    if missing:
        raise InvestigationError(
            "ZetarisSchemaError",
            "phoenix.revival_attempts is missing columns: " + ", ".join(missing),
            status_code=502,
        )
    return {
        "repo_id": blank_text(row.get("repo_id")),
        "fork_full_name": blank_text(row.get("fork_full_name")),
        "upstream_full_name": blank_text(row.get("upstream_full_name")),
        "stage": blank_text(row.get("stage")).lower(),
        "status": blank_text(row.get("status")).lower(),
        "ci_status": blank_text(row.get("ci_status")).lower(),
        "ci_run_url": blank_text(row.get("ci_run_url")),
        "ci_run_name": blank_text(row.get("ci_run_name")),
        "ci_run_event": blank_text(row.get("ci_run_event")),
        "commit_sha": blank_text(row.get("commit_sha")),
        "compare_url": blank_text(row.get("compare_url")),
        "default_branch": blank_text(row.get("default_branch")),
        "head_sha": blank_text(row.get("head_sha")),
        "upstream_base_sha": blank_text(row.get("upstream_base_sha")),
        "ci_head_repo": blank_text(row.get("ci_head_repo")),
        "workflow_path": blank_text(row.get("workflow_path")),
        "workflow_steps": blank_text(row.get("workflow_steps")),
        "workflow_runs_build": blank_text(row.get("workflow_runs_build")).lower(),
        "attempted_at": blank_text(row.get("attempted_at")),
        "data_origin": blank_text(row.get("data_origin")),
    }


def normalize_source(row: dict[str, Any]) -> dict[str, Any]:
    required = ("repo_id", "source_id", "kind", "url", "excerpt")
    missing = [column for column in required if column not in row]
    if missing:
        raise InvestigationError(
            "ZetarisSchemaError",
            "phoenix.revival_sources is missing columns: " + ", ".join(missing),
            status_code=502,
        )
    return {
        "repo_id": blank_text(row.get("repo_id")),
        "source_id": blank_text(row.get("source_id")),
        "kind": blank_text(row.get("kind")).lower(),
        "title": blank_text(row.get("title")),
        "url": blank_text(row.get("url")),
        "excerpt": blank_text(row.get("excerpt")),
        "data_origin": blank_text(row.get("data_origin")),
    }


_NOT_YET = "Revival has not yet been attempted."
_CANDIDATE = "No qualifying stage-1 revival success is recorded, so the verdict is CANDIDATE."
_BUILD_MARKERS = (
    "yarn test",
    "yarn build",
    "npm test",
    "npm run build",
    "pnpm test",
    "go test",
    "cargo test",
    "pytest",
    "mvn test",
    "gradle test",
    "dotnet test",
    "bundle exec",
    "ctest",
    "make test",
)


def _commands_present(attempt: dict[str, Any]) -> bool:
    steps = attempt.get("workflow_steps") or ""
    lowered = steps.lower()
    return any(marker in lowered for marker in _BUILD_MARKERS)


def qualify_revive_attempt(attempt: dict[str, Any]) -> tuple[bool, str]:
    """A stage-1 result counts only for an attempt commit on the fork.

    The workflow must show a build or test command. A workflow that only
    matches the name CI does not count. The flag text is the reason.
    """

    commit = attempt.get("commit_sha") or ""
    base = attempt.get("upstream_base_sha") or ""
    fork = attempt.get("fork_full_name") or ""
    head_repo = attempt.get("ci_head_repo") or ""
    path = attempt.get("workflow_path") or "unrecorded"
    steps = attempt.get("workflow_steps") or "unrecorded"
    if not commit:
        return False, "commit_sha is empty, so this row is not an attempt commit."
    if not base:
        return False, "upstream_base_sha is empty, so the row does not show the commit is ahead of upstream."
    if commit == base:
        return False, "commit_sha equals the upstream base SHA, so the CI run is not an attempt commit."
    if not fork or head_repo.casefold() != fork.casefold():
        return False, f"CI head repo {head_repo or 'empty'} is not the fork {fork or 'empty'}."
    flag = (attempt.get("workflow_runs_build") or "").lower()
    if flag == "false" or not _commands_present(attempt):
        return False, (
            f"Workflow {path} is flagged and not counted. Recorded steps: {steps}. "
            "The workflow name alone is not a build or test run."
        )
    return True, ""


def _latest_revive(attempts: list[dict[str, Any]]) -> dict[str, Any] | None:
    rows = [
        attempt
        for attempt in attempts
        if attempt.get("stage") == "revive"
        and attempt.get("status") in {"success", "failure"}
        and attempt.get("ci_run_url")
    ]
    if not rows:
        return None
    if all(attempt.get("attempted_at") for attempt in rows):
        return max(rows, key=lambda attempt: attempt.get("attempted_at") or "")
    return rows[-1]


def stage1_may_override(row: dict[str, Any], signals: list[dict[str, Any]]) -> tuple[bool, str]:
    """Stage 1 may override an upstream CI failure or unknown CI. Nothing else."""

    blocked = blocking_value_reason(row)
    if blocked:
        return False, blocked
    if not row["has_tests"]:
        return False, "has_tests is false"
    if not row["has_license_file"]:
        return False, "has_license_file is false"
    for signal in signals:
        if signal["signal"] == "tests_absent" and not signal["supports_revival"]:
            return False, "signal tests_absent says revival is not supported"
    return True, ""


def upgrade_result(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Stages refactor and upgrade are reported beside the verdict. They do not decide it."""

    stages = [
        {
            "stage": attempt["stage"],
            "status": attempt["status"],
            "ci_run_url": attempt["ci_run_url"],
            "commit_sha": attempt["commit_sha"],
            "fork_full_name": attempt["fork_full_name"],
        }
        for attempt in attempts
        if attempt["stage"] in {"refactor", "upgrade"}
    ]
    return {
        "changes_verdict": False,
        "note": "Refactor and upgrade stages do not change the verdict. The verdict uses stage revive only.",
        "stages": stages,
    }


def apply_revival_attempts(
    row: dict[str, Any],
    signals: list[dict[str, Any]],
    attempts: list[dict[str, Any]] | None,
) -> tuple[str, str, dict[str, Any]]:
    """Fold fork attempt rows into the row policy.

    ``attempts is None`` returns ``decide()`` unchanged. An empty list means no
    attempt was recorded. The latest revive row wins, so a later failure beats
    an earlier success. A qualifying success overrides only an upstream CI
    failure or unknown CI. NO_GO, missing tests, and a missing licence file stay.
    Refactor and upgrade never change the decision.
    """

    decision, reason = decide(row, signals)
    recorded = list(attempts or [])
    upgrade = upgrade_result(recorded)
    if attempts is None or decision == "NO_GO":
        return decision, reason, upgrade
    latest = _latest_revive(recorded)
    if latest:
        qualified, flag = qualify_revive_attempt(latest)
        note = alternatives_factor(row["similar_repo_count"])
        if qualified and latest["status"] == "failure":
            commit = f" for commit {latest['commit_sha']}" if latest["commit_sha"] else ""
            conclusion = latest["ci_status"] or "failure"
            cited = (
                f"Stage revive failed: eligible build/test CI concluded {conclusion} "
                f"at {latest['ci_run_url']}{commit}."
            )
            return "RESURRECTION_FAILED", f"{cited} {note}", upgrade
        if qualified and latest["status"] == "success" and latest["ci_status"] == "success":
            allowed, block = stage1_may_override(row, signals)
            if allowed:
                fork = latest["fork_full_name"] or "unrecorded fork"
                branch = latest["default_branch"] or "unrecorded"
                overridden = (
                    f"Stage revive succeeded: eligible build/test CI on fork {fork} "
                    f"default branch {branch} concluded success at {latest['ci_run_url']} "
                    f"for attempt commit {latest['commit_sha']} "
                    f"(upstream base {latest['upstream_base_sha']})."
                )
                return "RESURRECTED", f"{overridden} {note}", upgrade
            held = (
                f"{reason} A stage-1 CI success at {latest['ci_run_url']} does not override this block: {block}."
            )
            return decision, held, upgrade
        if flag:
            reason = f"{reason} {flag}"
    if latest is None and decision == "RESURRECTION_FAILED" and _NOT_YET not in reason:
        reason = f"{reason} {_NOT_YET}"
    return decision, reason, upgrade


def decide(row: dict[str, Any], signals: list[dict[str, Any]]) -> tuple[str, str]:
    note = alternatives_factor(row["similar_repo_count"])
    blocked = blocking_value_reason(row)
    if blocked:
        return "NO_GO", f"{blocked}. {note}"
    validation = validation_block_reason(row, signals)
    if validation:
        return "RESURRECTION_FAILED", f"{validation}. {note}"
    if ci_kind(row["ci_status"]) == "success":
        base = "licence, abandonment, distinct capability, tests, and a successful CI conclusion support revival"
    else:
        base = (
            "licence, abandonment, distinct capability, and tests support revival; "
            "CI is unknown and is not treated as a failure"
        )
    return "CANDIDATE", f"{base}. {_CANDIDATE} {note}"


def explanation(row: dict[str, Any], decision: str, reason: str) -> str:
    origin = row["data_origin"] or "unlabelled"
    return (
        f"Decision {decision} for {row['repo_id']}. {reason}. "
        f"Licence {row['licence_spdx']}, days_since_push {row['days_since_push']}, "
        f"similar_repo_count {row['similar_repo_count'] if row['similar_repo_count'] is not None else 'unknown'}, "
        f"unique_capability {row['unique_capability']!r}, "
        f"has_tests {row['has_tests']}, ci_status {row['ci_status']}. data_origin {origin}."
    )
