"""Deterministic filter for abandoned idea repositories.

The repository's own stars, forks, and issues are not read. Demand is external.
With no verified functional-equivalence mapping, a maintained free open-source
alternative that contains every required phrase is ALREADY_EXISTS. That path is
labelled phrase/keyword. The model may only propose a URL that is already in the rows.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.sql_catalog import ABANDONED_AFTER_DAYS, FREE_OSS_LICENCES

_BOILERPLATE_NAMES = frozenset(
    {
        "readme",
        "readme.md",
        "license",
        "license.md",
        "licence",
        "licence.md",
        ".gitignore",
        "package.json",
        "package-lock",
        "package-lock.json",
        "yarn.lock",
        "go.sum",
        "go.mod",
    }
)


def days_between(pushed_at: str | None, collected_at: str | None) -> int | None:
    """Whole days from the push date to the collection date. None when either is missing."""

    if not pushed_at or not collected_at:
        return None
    try:
        pushed = datetime.fromisoformat(str(pushed_at).replace("Z", "+00:00")).date()
        collected = datetime.fromisoformat(str(collected_at).replace("Z", "+00:00")).date()
    except ValueError:
        return None
    return (collected - pushed).days


def observed_days(row: dict[str, Any]) -> int | None:
    """Recompute the stall window. A stored count that disagrees fails closed."""

    computed = days_between(row.get("pushed_at"), row.get("collected_at"))
    stored = _int(row.get("days_since_push"))
    if computed is None:
        return None
    if stored is not None and stored != computed:
        return None
    return computed


def is_boilerplate(path: str) -> bool:
    normalized = path.replace("\\", "/").strip().lower()
    if not normalized:
        return True
    name = normalized.rsplit("/", 1)[-1]
    if name in _BOILERPLATE_NAMES:
        return True
    parts = [part for part in normalized.split("/") if part]
    if ".github" in parts:
        return True
    for index in range(len(parts) - 1):
        if parts[index] == "assets" and parts[index + 1] == "images":
            return True
    return False


def split_terms(value: str | None) -> list[str]:
    return [part.strip() for part in str(value or "").split("|") if part.strip()]


PHRASE_METHOD = "Method: phrase/keyword."


def maintenance_block(row: dict[str, Any]) -> str | None:
    """Why this alternative is not a maintained free OSI project. None when it is."""

    licence = str(row.get("licence_spdx") or "").strip()
    if licence not in FREE_OSS_LICENCES:
        return f"licence {licence or 'empty'} is not in the free OSI set"
    days = observed_days(row)
    if days is None:
        return "the maintained window cannot be computed"
    if days > ABANDONED_AFTER_DAYS:
        return f"last push is {days} days before collection, outside {ABANDONED_AFTER_DAYS} days"
    repo_id = str(row.get("repo_id") or "")
    alternative_id = str(row.get("alternative_id") or "")
    if not alternative_id or alternative_id.casefold() == repo_id.casefold():
        return "the alternative is missing or is the same repository"
    if not str(row.get("url") or "").strip():
        return "the alternative URL is empty"
    return None


def alternative_qualifies(row: dict[str, Any]) -> bool:
    """Phrase/keyword check. True only when the stored excerpt covers every required phrase."""

    if maintenance_block(row):
        return False
    terms = split_terms(row.get("required_terms"))
    if len(terms) < 2:
        return False
    candidate = str(row.get("candidate_excerpt") or "")
    excerpt = str(row.get("excerpt") or "")
    return all(_contains(candidate, term) and _contains(excerpt, term) for term in terms)


def demand_qualifies(row: dict[str, Any], core_terms: list[str], repo_id: str) -> bool:
    """External signals only. A search total never passes by itself."""

    kind = str(row.get("kind") or "")
    url = str(row.get("url") or "")
    if repo_id and repo_id.casefold() in url.casefold():
        return False
    excerpt = str(row.get("excerpt") or "")
    if kind == "github_search":
        return False
    if not core_terms or not any(_contains(excerpt, term) for term in core_terms):
        return False
    if kind == "github_issue":
        comments = _int(row.get("comments"))
        reactions = _int(row.get("reactions"))
        return (comments is not None and comments >= 2) or (reactions is not None and reactions >= 1)
    if kind == "related_repo":
        stars = _int(row.get("stars"))
        days = observed_days(row)
        return stars is not None and stars >= 100 and days is not None and days <= ABANDONED_AFTER_DAYS
    if kind == "registry_downloads":
        value = _int(row.get("metric_value"))
        return value is not None and value >= 1000
    return False


def decide_discovery(
    candidate: dict[str, Any],
    prior_rows: list[dict[str, Any]],
    demand_rows: list[dict[str, Any]],
) -> tuple[str, str]:
    """Idea quality, then prior art, then external demand. Stars are not an input."""

    blocked = idea_block_reason(candidate)
    if blocked:
        return "NO_GO", _phrase(blocked)
    qualifying = [row for row in prior_rows if alternative_qualifies(row)]
    if qualifying:
        return "ALREADY_EXISTS", _phrase(_already_exists_reason(qualifying))
    repo_id = str(candidate.get("repo_id") or "")
    terms = split_terms(candidate.get("core_terms"))
    passed = [row for row in demand_rows if demand_qualifies(row, terms, repo_id)]
    if not passed:
        return "NO_GO", _phrase("external demand for the problem was not shown")
    return "CANDIDATE", _phrase(_candidate_reason(passed[0]))


def accept_proposed_alternative(url: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A model URL counts only when it matches a row that already qualifies."""

    wanted = _norm_url(url)
    if not wanted:
        return None
    for row in rows:
        if _norm_url(str(row.get("url") or "")) == wanted and alternative_qualifies(row):
            return row
    return None


def showcase_prior_art_note(rows: list[dict[str, Any]]) -> str:
    """Note for a revival-set repository. It does not change that verdict."""

    qualifying = [row for row in rows if alternative_qualifies(row)]
    if qualifying:
        cited = ", ".join(str(row.get("alternative_id") or "") for row in qualifying)
        return (
            "Prior-art rows record a maintained free OSS alternative that covers every cited core phrase "
            f"({cited}). The revival verdict is unchanged because this repository is in the revival set, "
            "not the discovery cohort."
        )
    return (
        "Prior-art rows were recorded. None is a maintained free OSS alternative whose excerpt contains "
        "every required core phrase, so they do not change the revival verdict."
    )


def apply_market_overlay(
    decision: str,
    reason: str,
    discovery_rows: list[dict[str, Any]],
    prior_rows: list[dict[str, Any]],
    demand_rows: list[dict[str, Any]],
) -> tuple[str, str]:
    """Replace the revival verdict only when a discovery row exists. Empty rows are a no-op."""

    if discovery_rows:
        return decide_discovery(discovery_rows[0], prior_rows, demand_rows)
    if prior_rows:
        note = showcase_prior_art_note(prior_rows)
        if note not in reason:
            reason = reason.rstrip() + " " + note
        return decision, reason
    return decision, reason


def idea_block_reason(row: dict[str, Any]) -> str | None:
    description = str(row.get("description") or "")
    readme = str(row.get("readme_excerpt") or "")
    idea = description + "\n" + readme
    for label, key in (
        ("problem", "problem_excerpt"),
        ("target user", "user_excerpt"),
        ("scope", "scope_excerpt"),
    ):
        excerpt = str(row.get(key) or "").strip()
        if not excerpt or excerpt not in idea:
            return f"the README does not state a concrete {label}"
    paths = [part for part in str(row.get("code_paths") or "").split("|") if part]
    real_paths = [path for path in paths if not is_boilerplate(path)]
    if len(real_paths) < 2:
        return "the tree does not show at least two source paths beyond a template"
    computed = days_between(row.get("pushed_at"), row.get("collected_at"))
    stored = _int(row.get("days_since_push"))
    if computed is None:
        return "pushed_at or collected_at is missing, so the stall window cannot be checked"
    if stored is not None and stored != computed:
        return "days_since_push does not match pushed_at and collected_at"
    if computed < ABANDONED_AFTER_DAYS:
        return (
            f"the repository was pushed {computed} days before collection, "
            f"inside the {ABANDONED_AFTER_DAYS}-day stall window"
        )
    terms = split_terms(row.get("core_terms"))
    if len(terms) < 2:
        return "fewer than two core terms are recorded"
    for term in terms:
        if not _contains(idea, term):
            return f"core term {term!r} is not in the README or description"
    return None


def _phrase(reason: str) -> str:
    if PHRASE_METHOD in reason:
        return reason
    text = reason.rstrip()
    if not text.endswith("."):
        text += "."
    return text + " " + PHRASE_METHOD


def _already_exists_reason(rows: list[dict[str, Any]]) -> str:
    parts = []
    for row in rows:
        terms = ", ".join(split_terms(row.get("required_terms")))
        parts.append(
            f"{row.get('alternative_id')} ({row.get('url')}) is a maintained free OSS alternative "
            f"({row.get('licence_spdx')}, pushed {row.get('pushed_at')}, "
            f"{observed_days(row)} days before collection) whose excerpt contains every required term "
            f"({terms}). Excerpt: {row.get('excerpt')}"
        )
    return " ".join(parts)


def _candidate_reason(row: dict[str, Any]) -> str:
    return (
        f"External demand is {row.get('kind')} {row.get('url')}. "
        "No maintained free OSS alternative covers every required core phrase."
    )


def _contains(text: str, term: str) -> bool:
    return term.casefold() in text.casefold()


def _int(value: Any) -> int | None:
    text = str(value if value is not None else "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/").casefold()
