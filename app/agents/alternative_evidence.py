"""Carry source facts through model classification without accepting invented facts."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any
from urllib.parse import urlparse

from app.agents.repo_facts import _api_url


SOURCE_FACT_FIELDS = (
    "licence_spdx", "license", "stars", "pushed_at", "released_at", "archived", "forks",
    "free_tier", "self_hosted", "official", "commercial", "page_checked_at", "pricing_page",
    "page_kind", "host", "fetch_status", "facts_source_url", "facts_sha256",
    "page_text", "page_html", "upstream_project_url",
    "fork", "parent", "source_repo",
    "same_job",
    "title",
)


def source_facts(row: dict[str, Any]) -> dict[str, Any]:
    """Only the collected row supplies these fields; the model entry never does."""
    return {key: deepcopy(row[key]) for key in SOURCE_FACT_FIELDS if key in row}


def verified_context(text: str) -> dict[str, str]:
    # Keep the context used by the verifier, not only its 1,000-character display
    # excerpt. Otherwise a quote that was just verified can fail the next gate.
    return {"text": text}


def repair_fields(source_row: dict[str, Any], text: str) -> dict[str, Any]:
    """Source facts and verified context for a model-classified row. Empty unless the repair is on."""
    from app.agents import gate1_flag

    if not gate1_flag.enabled():
        return {}
    return {**source_facts(source_row), **verified_context(text)}


def trace_alternatives(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], int]:
    """Record gate inputs after hydration; never truncate a JSON proof mid-field."""
    payload = {"alternatives": [
        {**{key: row.get(key) for key in ("alternative_id", "url", "source", "overlap", "reason", "quotes", "quote_verified", "same_problem", "same_job", "substitution", "substitution_status")},
         "source_facts": {key: row[key] for key in SOURCE_FACT_FIELDS if key in row and key not in {"page_text", "page_html"}},
         "verified_text_sha256": hashlib.sha256(str(row.get("text") or row.get("excerpt") or "").encode("utf-8")).hexdigest()}
        for row in rows
    ]}
    return payload, max(20000, len(json.dumps(payload, ensure_ascii=False, default=str)) + 100)


def _identity(value: str) -> str:
    value = value.strip().removeprefix("web:")
    host, api = _api_url(value)
    if host and api:
        return api.casefold()
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return parsed.netloc.casefold() + parsed.path.rstrip("/") + ("?" + parsed.query if parsed.query else "")
    return value.casefold()


def match_alternative(named: str | None, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Exact canonical IDs/URLs, then a unique short name. Never substring prose."""
    if not named or not named.strip():
        return None
    target = _identity(named)
    matches = [row for row in rows if target and target in {_identity(str(row.get(k) or "")) for k in ("alternative_id", "url")}]
    if not matches and "/" not in named and ":" not in named:
        matches = [row for row in rows if named.casefold() in {
            str(row.get("alternative_id") or "").split("/")[-1].casefold(),
            str(row.get("title") or "").split(":", 1)[0].strip().casefold(),
        }]
    return matches[0] if len(matches) == 1 else None
