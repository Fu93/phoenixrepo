"""Maturity gate for an ALREADY_EXISTS refusal.

A verified full overlap is not enough. The alternative has to be the same job on a
comparable platform, maintained, mature or official, and reachable on the same terms
as the candidate. Anything else is kept as context the candidate must beat. It does
not refuse the repository, and it never produces RESURRECTED.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urlparse

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

# A docs page on one of these hosts is someone else's platform, not the vendor's own site.
_PLATFORM_SUFFIXES = (
    "medium.com",
    "dev.to",
    "hashnode.com",
    "hashnode.dev",
    "substack.com",
    "wordpress.com",
    "blogspot.com",
    "github.io",
    "gitlab.io",
    "readthedocs.io",
    "readthedocs.org",
    "gitbook.io",
    "notion.site",
    "wikipedia.org",
    "reddit.com",
    "stackoverflow.com",
    "stackexchange.com",
)
_FORGE_SUFFIXES = ("github.com", "gitlab.com", "codeberg.org", "bitbucket.org")
_DOCS_HOST_LABELS = {"docs", "support", "help", "developer", "developers", "documentation", "www"}
_DOCS_PATH_LABELS = {
    "docs",
    "documentation",
    "support",
    "guides",
    "guide",
    "integrations",
    "integration",
    "products",
    "product",
    "features",
    "feature",
    "help",
}
_BLOG_PATH_LABELS = {"blog", "posts", "articles", "news"}
_COMPOUND_TLDS = {("co", "uk"), ("com", "au"), ("co", "jp"), ("com", "br"), ("co", "nz")}
_PAID_ONLY = re.compile(
    r"paid[- ]only|paid plans? only|requires a paid|only available on (?:a |the )?paid|"
    r"subscription required|enterprise only|not available on the free",
    re.IGNORECASE,
)
_FREE_CORE = re.compile(
    r"free alternative|open[- ]source alternative|core value is being free|core value is free",
    re.IGNORECASE,
)


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


def _hostname(url: str) -> tuple[str, str]:
    raw = str(url or "").strip()
    if not raw:
        return "", ""
    if "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    return (parsed.hostname or "").lower().rstrip("."), parsed.path or ""


def _host_is(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def _page_url(alternative: dict[str, Any]) -> str:
    url = str(alternative.get("url") or "").strip()
    if url:
        return url
    alt = str(alternative.get("alternative_id") or "").strip()
    if alt.startswith("web:"):
        return alt[4:]
    if alt.startswith("http://") or alt.startswith("https://"):
        return alt
    return ""


def _brand_tokens(host: str) -> list[str]:
    labels = [part for part in host.split(".") if part and part != "www"]
    while labels and labels[0] in _DOCS_HOST_LABELS:
        labels = labels[1:]
    if len(labels) >= 3 and tuple(labels[-2:]) in _COMPOUND_TLDS:
        brand = labels[-3]
    elif len(labels) >= 2:
        brand = labels[-2]
    else:
        brand = labels[0] if labels else ""
    tokens: list[str] = []
    if brand:
        tokens.append(brand)
        for suffix in ("hq", "project"):
            if brand.endswith(suffix) and len(brand) - len(suffix) >= 4:
                tokens.append(brand[: -len(suffix)])
    return [token for token in tokens if len(token) >= 4]


def _product_text(alternative: dict[str, Any]) -> str:
    parts = [str(alternative.get(key) or "") for key in ("title", "name", "product", "description", "excerpt")]
    parts.extend(str(quote) for quote in (alternative.get("quotes") or []))
    return " ".join(parts)


def _brand_in_text(tokens: list[str], text: str) -> bool:
    folded = text.casefold()
    for token in tokens:
        if re.search(rf"(?<![a-z0-9]){re.escape(token)}", folded):
            return True
    return False


def _docs_shaped(host: str, path: str) -> bool:
    labels = [part for part in host.split(".") if part]
    if labels and labels[0] in _DOCS_HOST_LABELS and labels[0] != "www":
        return True
    parts = [part for part in path.lower().split("/") if part]
    return any(part in _DOCS_PATH_LABELS for part in parts)


def _page_text(alternative: dict[str, Any]) -> str:
    return " ".join(
        str(alternative.get(key) or "")
        for key in ("title", "name", "product", "description", "excerpt", "readme", "text")
    )


def _verified_quote(alternative: dict[str, Any]) -> bool:
    """The official-docs shortcut requires a quote already checked against the page text."""
    if alternative.get("quote_verified") is not True:
        return False
    quotes = [str(quote).strip() for quote in (alternative.get("quotes") or []) if str(quote).strip()]
    if not quotes:
        return False
    folded = " ".join(_page_text(alternative).split()).casefold()
    if not folded:
        return False
    return all(" ".join(quote.split()).casefold() in folded for quote in quotes)


def first_party_vendor_docs(alternative: dict[str, Any]) -> tuple[bool, str]:
    """Whether this page is the vendor's own docs or feature page for the product it describes.

    The host has to be that vendor's domain (``pagerduty.com/docs``, ``docs.datadoghq.com``,
    ``support.atlassian.com``). A code host, a publisher, a docs platform, and a blog do not
    qualify, and neither does a docs URL whose text names a different product. Stars and
    push dates are not part of this check.
    """
    host, path = _hostname(_page_url(alternative))
    if not host:
        return False, "not a docs or feature page"
    if any(_host_is(host, suffix) for suffix in _FORGE_SUFFIXES):
        return False, "a code host is not a vendor docs page"
    if any(_host_is(host, suffix) for suffix in _PLATFORM_SUFFIXES):
        return False, "a publisher or docs platform is not the vendor's own domain"
    parts = [part for part in path.lower().split("/") if part]
    if any(part in _BLOG_PATH_LABELS for part in parts):
        return False, "a blog path is not a vendor feature page"
    if not _docs_shaped(host, path):
        return False, "not a docs or feature page"
    tokens = _brand_tokens(host)
    if not tokens or not _brand_in_text(tokens, _product_text(alternative)):
        return False, f"domain {host} is not the vendor of the product the page describes"
    return True, f"first-party vendor docs on {host}"


def _paid_only(alternative: dict[str, Any]) -> bool:
    blob = _product_text(alternative)
    blob = f"{blob} {alternative.get('reason') or ''}"
    return _PAID_ONLY.search(blob) is not None


def _core_value_is_free(candidate: dict[str, Any]) -> bool:
    blob = " ".join(
        str(candidate.get(key) or "")
        for key in ("description", "readme_excerpt", "problem_excerpt", "tagline", "pitch")
    )
    return _FREE_CORE.search(blob) is not None


def _licence_ok(alternative: dict[str, Any]) -> bool:
    licence = str(alternative.get("licence_spdx") or alternative.get("license") or "").strip().upper()
    if licence in PERMISSIVE:
        return True
    if alternative.get("free_tier") or alternative.get("self_hosted"):
        return True
    return False


def _is_web_row(alternative: dict[str, Any]) -> bool:
    source = str(alternative.get("source") or "").strip().lower()
    alt = str(alternative.get("alternative_id") or "").strip().lower()
    return source == "web" or alt.startswith("web:")


def _forge_repo_page(alternative: dict[str, Any]) -> bool:
    """A GitHub, GitLab, Codeberg, or Bitbucket repository page, including a web hit of one."""
    if _host(alternative) in {"github", "gitlab", "codeberg"}:
        return True
    source = str(alternative.get("source") or "").strip().lower()
    if source in {"github", "gitlab", "codeberg", "bitbucket"}:
        return True
    pages = [_page_url(alternative)]
    alt = str(alternative.get("alternative_id") or "")
    if alt.startswith("web:"):
        pages.append(alt[4:])
    for raw in pages:
        host_name, _path = _hostname(raw)
        if host_name and any(_host_is(host_name, suffix) for suffix in _FORGE_SUFFIXES):
            return True
    return False


def non_forge_web_page(alternative: dict[str, Any]) -> bool:
    """A web page that is not a GitHub, GitLab, Codeberg, or Bitbucket repository page."""
    if _forge_repo_page(alternative):
        return False
    source = str(alternative.get("source") or "").strip().lower()
    if source in {"github", "gitlab", "codeberg", "bitbucket", "npm", "pypi"}:
        return False
    alt = str(alternative.get("alternative_id") or "").strip().lower()
    if alt.startswith(("npm:", "pypi:", "crates:")):
        return False
    if _is_web_row(alternative):
        return True
    url = _page_url(alternative)
    return url.startswith("http://") or url.startswith("https://")


def _placeholder_web_stars(alternative: dict[str, Any], stars: int | None) -> bool:
    """Web rows are stored with stars=0. That zero is a placeholder and is never overwritten."""
    return stars == 0 and _is_web_row(alternative)


def _access_fields_absent(alternative: dict[str, Any]) -> bool:
    """True when licence, free tier, and self-hosted were never filled in."""
    licence = str(alternative.get("licence_spdx") or alternative.get("license") or "").strip()
    if licence:
        return False
    if "free_tier" in alternative and alternative.get("free_tier") is not None:
        return False
    if "self_hosted" in alternative and alternative.get("self_hosted") is not None:
        return False
    return True


def _maintenance_date_unknown(alternative: dict[str, Any]) -> bool:
    """No push or release date. An official page_checked_at counts as a known maintenance date."""
    if _as_date(alternative.get("pushed_at")) is not None or _as_date(alternative.get("released_at")) is not None:
        return False
    official = alternative.get("official") or alternative.get("commercial")
    if official and _as_date(alternative.get("page_checked_at")) is not None:
        return False
    return True


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
    ``unknown_reasons`` and ``proven_reasons`` classify why it did not block. They do not
    change ``blocks``, ``role``, or ``verdict_if_blocked``.
    """
    reasons: list[str] = []
    unknown: list[str] = []
    proven: list[str] = []
    date_unknown = _maintenance_date_unknown(alternative)

    def add(bucket: list[str], item: str) -> None:
        if item not in bucket:
            bucket.append(item)

    def finish(blocks: bool, role: str, why: list[str], kind: str) -> dict[str, Any]:
        # A verified paid-only quote is a proven non-match. It does not change blocks.
        if not blocks and _paid_only(alternative) and _verified_quote(alternative):
            add(proven, "paid-only")
        return _result(
            blocks,
            role,
            why,
            kind,
            unknown_reasons=unknown,
            proven_reasons=proven,
            maintenance_date_unknown=date_unknown,
        )

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
            add(unknown, "host fetch")
            add(unknown, "push/release date")
            add(unknown, "stars")
            return finish(False, "source_error", reasons, kind)

    if kind in NOT_AN_ALTERNATIVE:
        reasons.append(f"{kind} is not an alternative")
        add(proven, f"page kind {kind}")
    if not same_job or (overlap and overlap != "full"):
        if overlap == "partial":
            reasons.append("overlap is partial, not the same job on the same platform")
            add(proven, "partial overlap")
        elif not same_job:
            reasons.append("not the same job, user, and platform")
            add(proven, "different job")
        elif overlap and overlap != "full":
            reasons.append(f"overlap is {overlap}")
            add(proven, f"overlap {overlap}")

    domain_ok, domain_why = first_party_vendor_docs(alternative)
    # A verified quote stays in front of the shortcut. Stars and push dates do not.
    # A domain miss explains why the shortcut did not apply. It does not veto a row
    # that already passes on stars, a release date, or an explicit official flag.
    first_party = domain_ok and _verified_quote(alternative)
    domain_note = domain_why if (not domain_ok and domain_why.startswith("domain ")) else ""

    if alternative.get("archived") is True:
        maintained, maintain_why = False, "archived"
    elif first_party:
        maintained, maintain_why = True, "maintained: " + domain_why
    else:
        maintained, maintain_why = _maintained(alternative, as_of)
    if not maintained:
        reasons.append(maintain_why)
        if alternative.get("archived") is True or maintain_why == "archived":
            add(proven, "archived")
        elif maintain_why == "no push or release date":
            add(unknown, "push/release date")
        else:
            add(proven, "stale")
    if first_party:
        mature, mature_why = True, "official: " + domain_why
    else:
        mature, mature_why = _mature(candidate, alternative)
    if not mature:
        reasons.append("not mature or official: " + mature_why)
        if stars is None or _placeholder_web_stars(alternative, stars):
            add(unknown, "stars")
        else:
            add(proven, "stars below bar")
    cand_free = candidate.get("free", True) is not False and candidate.get("self_hostable", True) is not False
    access_ok = True
    access_why = ""
    if first_party:
        # Paid-only is a mismatch only when the quote says so and the candidate's core
        # value is being free. A vendor feature that the quote does not price is kept.
        if _paid_only(alternative) and _core_value_is_free(candidate) and cand_free:
            access_ok = False
            access_why = (
                "access mismatch: the quote shows the alternative is paid-only "
                "and the candidate's core value is being free"
            )
        elif _paid_only(alternative):
            access_why = (
                "access kept: the quote shows a paid-only feature, "
                "and the candidate's core value is not being free"
            )
        else:
            access_why = "access kept: the quote does not show the alternative is paid-only"
    elif cand_free and not _licence_ok(alternative):
        access_ok = False
        access_why = "candidate is free and self-hostable; the alternative has no free tier or permissive licence"
        if _access_fields_absent(alternative):
            add(unknown, "licence/free tier")
        else:
            add(proven, "licence not permissive")
    if not access_ok and access_why:
        reasons.append(access_why)

    if fetch_status == "source_error" and stars is None:
        reasons.append("host API returned no facts, so this alternative cannot block")
        add(unknown, "host fetch")
        add(unknown, "stars")
        if domain_note:
            reasons.append(domain_note)
        return finish(False, "source_error", reasons, kind)

    hard_fail = [r for r in reasons if r]
    if (
        not hard_fail
        and same_job
        and overlap == "full"
        and kind not in NOT_AN_ALTERNATIVE
        and maintained
        and mature
        and access_ok
    ):
        notes = [maintain_why, mature_why]
        if access_why:
            notes.append(access_why)
        return _result(True, "block", notes, kind, maintenance_date_unknown=date_unknown)

    # A mature partial overlap still needs a person. An immature one does not refuse.
    if (
        overlap == "partial"
        and alternative.get("same_problem") is True
        and kind not in NOT_AN_ALTERNATIVE
        and maintained
        and mature
        and access_ok
        and fetch_status != "source_error"
    ):
        partial_reasons = list(reasons or ["partial overlap of a maintained product"])
        if access_why:
            partial_reasons.append(access_why)
        if domain_note:
            partial_reasons.append(domain_note)
        return finish(False, "mature_partial", partial_reasons, kind)

    role = "not_alternative" if kind in NOT_AN_ALTERNATIVE else "must_beat"
    if domain_note:
        reasons.append(domain_note)
    return finish(False, role, reasons or ["did not pass A-E"], kind)


def _result(
    blocks: bool,
    role: str,
    reasons: list[str],
    kind: str,
    *,
    unknown_reasons: list[str] | None = None,
    proven_reasons: list[str] | None = None,
    maintenance_date_unknown: bool = False,
) -> dict[str, Any]:
    return {
        "blocks": blocks,
        "role": role,
        "reasons": reasons,
        "unknown_reasons": list(unknown_reasons or []),
        "proven_reasons": list(proven_reasons or []),
        "maintenance_date_unknown": maintenance_date_unknown,
        "page_kind": kind,
        "verdict_if_blocked": "ALREADY_EXISTS" if blocks else None,
    }
