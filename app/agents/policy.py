"""Evidence policy for the terminal state.

The policy reads Zetaris rows. A model may phrase the explanation. It cannot
change a row, and it cannot receive RESURRECTED when the rows do not support it.
"""

from __future__ import annotations

from typing import Any

from app.errors import InvestigationError
from app.sql_catalog import ABANDONED_AFTER_DAYS, PERMISSIVE_LICENCES, SIMILAR_REPO_BLOCK


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
        similar = int(float(row["similar_repo_count"]))
    except (TypeError, ValueError) as exc:
        raise InvestigationError(
            "ZetarisSchemaError",
            "days_since_push and similar_repo_count must be numbers.",
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


def blocking_value_reason(row: dict[str, Any]) -> str | None:
    if row["licence_spdx"] not in PERMISSIVE_LICENCES:
        return f"licence {row['licence_spdx'] or 'missing'} is not in the permissive set"
    if row["successor_url"]:
        return "a successor URL is recorded"
    if row["days_since_push"] < ABANDONED_AFTER_DAYS:
        return f"last push was {row['days_since_push']} days ago, under the {ABANDONED_AFTER_DAYS}-day abandonment bar"
    if not row["unique_capability"] or row["similar_repo_count"] >= SIMILAR_REPO_BLOCK:
        return (
            f"similar_repo_count is {row['similar_repo_count']} and unique_capability is "
            f"{row['unique_capability']!r}"
        )
    return None


def validation_block_reason(row: dict[str, Any], signals: list[dict[str, Any]]) -> str | None:
    if not row["has_tests"]:
        return "has_tests is false"
    if not row["has_license_file"]:
        return "has_license_file is false"
    if row["ci_status"] != "success":
        return f"ci_status is {row['ci_status'] or 'missing'}"
    for signal in signals:
        if signal["signal"] in {"tests_absent", "ci_failure"} and not signal["supports_revival"]:
            return f"signal {signal['signal']} says revival is not supported"
    return None


def decide(row: dict[str, Any], signals: list[dict[str, Any]]) -> tuple[str, str]:
    blocked = blocking_value_reason(row)
    if blocked:
        return "NO_GO", blocked
    validation = validation_block_reason(row, signals)
    if validation:
        return "RESURRECTION_FAILED", validation
    return "RESURRECTED", "licence, abandonment, distinct capability, tests, and CI all have supporting rows"


def explanation(row: dict[str, Any], decision: str, reason: str) -> str:
    origin = row["data_origin"] or "unlabelled"
    return (
        f"Decision {decision} for {row['repo_id']}. {reason}. "
        f"Licence {row['licence_spdx']}, days_since_push {row['days_since_push']}, "
        f"similar_repo_count {row['similar_repo_count']}, unique_capability {row['unique_capability']!r}, "
        f"has_tests {row['has_tests']}, ci_status {row['ci_status']}. data_origin {origin}."
    )
