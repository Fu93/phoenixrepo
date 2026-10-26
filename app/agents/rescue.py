"""Rescue-potential score for a repository that stalled for engineering reasons.

Computed from repository facts (a GitHub API payload, or the same fields already
stored on a discovery row). It ranks repositories. It does not change a verdict,
and it cannot turn ALREADY_EXISTS or NO_GO into CANDIDATE or RESURRECTED.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

_FAILURE_TITLE = re.compile(
    r"can.?t use|cannot use|no hit|doesn.?t work|does not work|not work|error|deprecated|broken|fail|no effect|no data",
    re.IGNORECASE,
)


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def failure_title(title: str) -> bool:
    return bool(_FAILURE_TITLE.search(str(title or "")))


def count_external_breakage(issues: list[dict[str, Any]], owner: str) -> int:
    """Open issues by someone other than the owner, 0-1 comments, failure-style title."""
    owner_l = owner.strip().lower()
    count = 0
    for issue in issues:
        if issue.get("pull_request"):
            continue
        author = str((issue.get("user") or {}).get("login") or issue.get("author") or "").lower()
        if owner_l and author == owner_l:
            continue
        comments = _int(issue.get("comments"))
        if comments is None or comments > 1:
            continue
        if failure_title(str(issue.get("title") or "")):
            count += 1
    return count


def rescue_potential(facts: dict[str, Any], *, as_of: date) -> dict[str, Any]:
    """Components and a rank score. ``changes_verdict`` is always false."""
    stars = _int(facts.get("stars") if facts.get("stars") is not None else facts.get("stargazers_count"))
    forks = _int(facts.get("forks") if facts.get("forks") is not None else facts.get("forks_count"))
    open_issues = _int(facts.get("open_issues") if facts.get("open_issues") is not None else facts.get("open_issues_count"))
    pushed = _date(facts.get("pushed_at"))
    stale = (as_of - pushed).days if pushed is not None else None
    months = (stale / 30.44) if stale is not None else None
    stars_vs = round(stars / months, 2) if stars is not None and months and months > 0 else None
    breakage = facts.get("external_breakage")
    breakage_n = _int(breakage) if breakage is not None else None
    ci_state = facts.get("ci_state") or "not_fetched"
    dependabot = facts.get("dependabot_open")
    renovate = facts.get("renovate_open")
    score = 0.0
    if stars is not None:
        score += stars
    if forks is not None:
        score += 2 * forks
    if breakage_n is not None:
        score += 25 * breakage_n
    if stale is not None:
        score += min(stale, 2000) / 10.0
    if ci_state in {"failure", "ci_configured_zero_runs", "action_required", "cancelled", "timed_out", "startup_failure"}:
        score += 50
    for extra in (dependabot, renovate):
        n = _int(extra)
        if n is not None:
            score += 10 * n
    return {
        "score": round(score, 2),
        "changes_verdict": False,
        "source": facts.get("source") or "snapshot",
        "components": {
            "stars": stars,
            "forks": forks,
            "open_issues_count": open_issues,
            "external_breakage": breakage_n,
            "staleness_days": stale,
            "stars_vs_staleness": stars_vs,
            "ci_state": ci_state,
            "ci_date": facts.get("ci_date"),
            "dependabot_open": _int(dependabot) if dependabot is not None else None,
            "renovate_open": _int(renovate) if renovate is not None else None,
        },
    }


def from_candidate_row(candidate: dict[str, Any], *, as_of: date) -> dict[str, Any]:
    """Score the fields already stored on a discovery row. Missing API parts stay null."""
    facts = {
        "stars": candidate.get("stargazers_count", candidate.get("stars")),
        "forks": candidate.get("forks_count", candidate.get("forks")),
        "open_issues": candidate.get("open_issues", candidate.get("open_issues_count")),
        "pushed_at": candidate.get("pushed_at"),
        "external_breakage": None,
        "ci_state": "not_fetched",
        "ci_date": None,
        "dependabot_open": None,
        "renovate_open": None,
        "source": "snapshot",
    }
    return rescue_potential(facts, as_of=as_of)
