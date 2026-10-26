"""Maturity gate for an ALREADY_EXISTS refusal.

A verified full overlap is not enough. The alternative has to be the same job on a
comparable platform, maintained, mature or official, and reachable on the same terms
as the candidate. Anything else is kept as context the candidate must beat. It does
not refuse the repository, and it never produces RESURRECTED.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

# OSI-style licences a free, self-hostable candidate can be matched against.
PERMISSIVE = {
    "MIT",
    "APACHE-2.0",
    "BSD-2-CLAUSE",
    "BSD-3-CLAUSE",
    "BSD-3",
    "ISC",
    "MPL-2.0",
    "UNLICENSE",
    "0BSD",
    "ZLIB",
    "BSL-1.0",
}

# These are not products a user could adopt instead of the candidate.
NOT_AN_ALTERNATIVE = {
    "syscall",
    "standard",
    "article",
    "listicle",
    "category",
    "aggregator",
    "docs_other_mode",
}

MAINTENANCE_DAYS = 365


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_date(value: Any) -> date | None:
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
        pass
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _host(alternative: dict[str, Any]) -> str:
    named = str(alternative.get("host") or "").strip().lower()
    if named:
        return named
    url = str(alternative.get("url") or alternative.get("alternative_id") or "").lower()
    if "codeberg.org" in url:
        return "codeberg"
    if "gitlab.com" in url or "gitlab." in url:
        return "gitlab"
    if "github.com" in url or (url.count("/") == 1 and not url.startswith("web:")):
        return "github"
    return "other"


def classify_page(alternative: dict[str, Any]) -> str:
    """Page kind. An explicit page_kind on the row wins. Otherwise a few URL shapes."""
    explicit = str(alternative.get("page_kind") or "").strip().lower()
    if explicit:
        return explicit
    blob = " ".join(
        str(alternative.get(key) or "")
        for key in ("alternative_id", "url", "description", "excerpt", "title")
    ).lower()
    if "perf_event_open" in blob or blob.strip() in {"syscall", "a syscall"}:
        return "syscall"
    if "/category" in blob or "category page" in blob:
        return "category"
    if "best " in blob and ("extension" in blob or "tools" in blob or "listicle" in blob):
        return "listicle"
    return "product"


def _licence_ok(alternative: dict[str, Any]) -> bool:
    licence = str(alternative.get("licence_spdx") or alternative.get("license") or "").strip().upper()
    if licence in PERMISSIVE:
        return True
    if alternative.get("free_tier") or alternative.get("self_hosted"):
        return True
    return False


def _maintained(alternative: dict[str, Any], as_of: date) -> tuple[bool, str]:
    if alternative.get("archived") is True:
        return False, "archived"
    pushed = _as_date(alternative.get("pushed_at"))
    released = _as_date(alternative.get("released_at"))
    dates = [d for d in (pushed, released) if d is not None]
    latest = max(dates) if dates else None
    if latest is None and (alternative.get("official") or alternative.get("commercial")):
        latest = _as_date(alternative.get("page_checked_at"))
    if latest is None:
        return False, "no push or release date"
    age = (as_of - latest).days
    if age > MAINTENANCE_DAYS:
        return False, f"last update {latest.isoformat()} is {age} days before {as_of.isoformat()}"
    return True, f"updated {latest.isoformat()}"


def _mature(candidate: dict[str, Any], alternative: dict[str, Any]) -> tuple[bool, str]:
    stars = _as_int(alternative.get("stars"))
    cand = _as_int(candidate.get("stars") if candidate.get("stars") is not None else candidate.get("stargazers_count")) or 0
    star_ok = stars is not None and stars >= 100 and stars >= (cand / 2)
    official = bool(alternative.get("official") or alternative.get("commercial"))
    dated = _as_date(alternative.get("released_at")) is not None or _as_date(alternative.get("page_checked_at")) is not None
    pricing = bool(alternative.get("pricing_page") or alternative.get("free_tier"))
    if star_ok:
        return True, f"{stars} stars, at least half of the candidate's {cand}"
    if official and (dated or pricing) and classify_page(alternative) == "product":
        return True, "official or commercial product with a dated release or a pricing page"
    return False, f"stars={stars}, candidate_stars={cand}, official={official}"


def evaluate_blocker(candidate: dict[str, Any], alternative: dict[str, Any], *, as_of: date) -> dict[str, Any]:
    """Decide whether this alternative may refuse the candidate.

    ``blocks`` is true only when A-E all hold. The verdict string is never RESURRECTED.
    """
    reasons: list[str] = []
    kind = classify_page(alternative)
    overlap = str(alternative.get("overlap") or "").strip().lower()
    if alternative.get("same_job") is None:
        same_job = alternative.get("same_problem") is True and overlap == "full"
    else:
        same_job = bool(alternative.get("same_job"))
    host = _host(alternative)
    stars = _as_int(alternative.get("stars"))
    has_git_date = _as_date(alternative.get("pushed_at")) or _as_date(alternative.get("released_at"))
    fetch_status = str(alternative.get("fetch_status") or "")
    if host in {"github", "codeberg", "gitlab"} and stars is None and not has_git_date:
        if fetch_status in {"", "source_error", "not_fetched"}:
            reasons.append(f"{host} maintenance fields were not fetched")
            return _result(False, "source_error", reasons, kind)

    if kind in NOT_AN_ALTERNATIVE:
        reasons.append(f"{kind} is not an alternative")
    if not same_job or (overlap and overlap != "full"):
        if overlap == "partial":
            reasons.append("overlap is partial, not the same job on the same platform")
        elif not same_job:
            reasons.append("not the same job, user, and platform")
        elif overlap and overlap != "full":
            reasons.append(f"overlap is {overlap}")

    maintained, maintain_why = _maintained(alternative, as_of)
    if not maintained:
        reasons.append(maintain_why)
    mature, mature_why = _mature(candidate, alternative)
    if not mature:
        reasons.append("not mature or official: " + mature_why)
    cand_free = candidate.get("free", True) is not False and candidate.get("self_hostable", True) is not False
    if cand_free and not _licence_ok(alternative):
        reasons.append("candidate is free and self-hostable; the alternative has no free tier or permissive licence")

    if fetch_status == "source_error" and stars is None:
        reasons.append("host API returned no facts, so this alternative cannot block")
        return _result(False, "source_error", reasons, kind)

    hard_fail = [r for r in reasons if r]
    if not hard_fail and same_job and overlap == "full" and kind not in NOT_AN_ALTERNATIVE and maintained and mature and (not cand_free or _licence_ok(alternative)):
        return _result(True, "block", [maintain_why, mature_why], kind)

    # A mature partial overlap still needs a person. An immature one does not refuse.
    if (
        overlap == "partial"
        and alternative.get("same_problem") is True
        and kind not in NOT_AN_ALTERNATIVE
        and maintained
        and mature
        and (not cand_free or _licence_ok(alternative))
        and fetch_status != "source_error"
    ):
        return _result(False, "mature_partial", reasons or ["partial overlap of a maintained product"], kind)

    role = "not_alternative" if kind in NOT_AN_ALTERNATIVE else "must_beat"
    return _result(False, role, reasons or ["did not pass A-E"], kind)


def _result(blocks: bool, role: str, reasons: list[str], kind: str) -> dict[str, Any]:
    return {
        "blocks": blocks,
        "role": role,
        "reasons": reasons,
        "page_kind": kind,
        "verdict_if_blocked": "ALREADY_EXISTS" if blocks else None,
    }
