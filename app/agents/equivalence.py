"""Functional-equivalence prior art.

The model proposes capabilities and a covered / partial / missing map.
This module does not trust that map until every quoted excerpt is an exact
substring of stored fetched text and the stored content hash matches the text.
ALREADY_EXISTS requires one maintained free OSI alternative to cover every
core capability. Anything short of that is a gap list. With no verified
mapping, the caller keeps the labelled phrase/keyword fallback.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.agents.market_filter import (
    decide_discovery,
    demand_qualifies,
    idea_block_reason,
    maintenance_block,
    observed_days,
    split_terms,
)
from app.sql_catalog import ABANDONED_AFTER_DAYS, FREE_OSS_LICENCES

FUNCTIONAL_METHOD = "Method: functional-equivalence."
# GitHub's licence API returns NOASSERTION for Anki. This sentence is the
# grant in the stored LICENSE file, not a README badge.
AGPL_GRANT = "Anki is licensed under the GNU Affero General Public License, version 3 or"
STATUSES = frozenset({"covered", "partial", "missing"})


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evidence_from_rows(
    candidate: dict[str, Any],
    prior_rows: list[dict[str, Any]],
    demand_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Stored excerpts the guard can check. Hashes are added by evidence_index."""

    repo_id = str(candidate.get("repo_id") or "")
    items = [
        {
            "id": f"candidate:{repo_id}",
            "url": str(candidate.get("html_url") or ""),
            "text": "\n".join(
                part
                for part in (
                    str(candidate.get("description") or ""),
                    str(candidate.get("readme_excerpt") or ""),
                    str(candidate.get("problem_excerpt") or ""),
                    str(candidate.get("user_excerpt") or ""),
                    str(candidate.get("scope_excerpt") or ""),
                )
                if part
            ),
        }
    ]
    for row in prior_rows:
        items.append(
            {
                "id": f"prior:{row.get('alternative_id')}",
                "url": str(row.get("url") or ""),
                "text": str(row.get("excerpt") or ""),
            }
        )
    for row in demand_rows or []:
        items.append(
            {
                "id": f"demand:{row.get('signal_id')}",
                "url": str(row.get("url") or ""),
                "text": str(row.get("excerpt") or ""),
            }
        )
    return items


def stamp_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy evidence rows. A stored hash that is not sha256 of the text is kept and fails later."""

    stamped = []
    for item in items:
        text = str(item.get("text") or "")
        copy = dict(item)
        copy["text"] = text
        digest = text_hash(text)
        claimed = str(item.get("content_hash") or "")
        copy["content_hash"] = claimed or digest
        copy["hash_matches_text"] = copy["content_hash"] == digest
        stamped.append(copy)
    return stamped


def evidence_index(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("id") or ""): item for item in stamp_evidence(items) if item.get("id")}


def quote_verified(quote: str, item: dict[str, Any] | None, claimed_hash: str | None = None) -> bool:
    """True when quote is an exact substring and the stored hash matches the text."""

    if item is None:
        return False
    text = str(item.get("text") or "")
    digest = text_hash(text)
    if not item.get("hash_matches_text", str(item.get("content_hash") or "") == digest):
        return False
    if str(item.get("content_hash") or "") != digest:
        return False
    if claimed_hash and str(claimed_hash) != digest:
        return False
    snippet = str(quote or "")
    if not snippet:
        return False
    return snippet in text


def functional_licence(row: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> str | None:
    """SPDX id from the row, or AGPL-3.0 when the stored licence file says so."""

    spdx = str(row.get("licence_spdx") or "").strip()
    if spdx in FREE_OSS_LICENCES:
        return spdx
    item = evidence.get(str(row.get("licence_evidence_id") or ""))
    if item and quote_verified(AGPL_GRANT, item):
        return "AGPL-3.0"
    return None


def functional_maintenance_block(row: dict[str, Any], evidence: dict[str, dict[str, Any]]) -> str | None:
    """Same window as the phrase guard. Licence may come from a stored licence file."""

    if functional_licence(row, evidence) is None:
        return maintenance_block(row) or "licence is not a verified free OSI id"
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


def verify_mapping(
    mapping: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    prior_rows: list[dict[str, Any]],
) -> tuple[bool, str, list[dict[str, Any]]]:
    """Return whether the capability list is grounded, and each cell after quote checks.

    A cell whose quote is not in the stored text becomes status unverified.
    The capability list itself must be fully grounded or the mapping is rejected.
    """

    capabilities = mapping.get("capabilities")
    cells = mapping.get("mappings")
    if not isinstance(capabilities, list) or not capabilities:
        return False, "the mapping has no capabilities", []
    if not isinstance(cells, list):
        return False, "the mapping has no mappings list", []
    seen: set[str] = set()
    for capability in capabilities:
        if not isinstance(capability, dict):
            return False, "a capability is not an object", []
        cap_id = str(capability.get("id") or "").strip()
        citation = str(capability.get("citation") or "")
        evidence_id = str(capability.get("evidence_id") or "")
        if not cap_id or cap_id in seen:
            return False, f"capability id {cap_id or 'empty'} is missing or repeated", []
        seen.add(cap_id)
        item = evidence.get(evidence_id)
        if not quote_verified(citation, item, capability.get("content_hash")):
            return False, f"capability {cap_id} citation is not in the stored evidence", []
    verified_cells = []
    for cell in cells:
        if not isinstance(cell, dict):
            verified_cells.append({"status": "unverified", "reason": "cell is not an object"})
            continue
        status = str(cell.get("status") or "").strip().lower()
        quote = str(cell.get("quote") or "")
        evidence_id = str(cell.get("evidence_id") or "")
        item = evidence.get(evidence_id)
        url = _norm_url(str(cell.get("alternative_url") or ""))
        row = _row_for_url(url, prior_rows)
        checked = dict(cell)
        checked["status_proposed"] = status
        if status not in STATUSES:
            checked["status"] = "unverified"
            checked["reason"] = "status is not covered, partial, or missing"
        elif row is None:
            checked["status"] = "unverified"
            checked["reason"] = "alternative URL is not a stored prior-art row"
        elif item is None or _norm_url(str(item.get("url") or "")) != _norm_url(str(row.get("url") or "")):
            checked["status"] = "unverified"
            checked["reason"] = "evidence URL does not match the alternative URL"
        elif status == "missing" and not quote:
            checked["status"] = "missing"
            checked["reason"] = ""
        elif not quote_verified(quote, item, cell.get("content_hash")):
            checked["status"] = "unverified"
            checked["reason"] = "quote is not an exact substring of the stored text"
        else:
            checked["status"] = status
            checked["reason"] = ""
            checked["content_hash"] = item.get("content_hash")
        verified_cells.append(checked)
    return True, "", verified_cells


def judge_discovery(
    candidate: dict[str, Any],
    prior_rows: list[dict[str, Any]],
    demand_rows: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]] | None = None,
    mapping: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Idea quality, then a verified mapping, else the labelled phrase fallback.

    A gap list becomes CANDIDATE only when external demand also passes.
    Demand was not repealed. A search total still does not pass by itself.
    """

    blocked = idea_block_reason(candidate)
    if blocked:
        text = blocked if blocked.endswith(".") else blocked + "."
        return "NO_GO", text + " Method: idea-quality."
    if mapping is None:
        return decide_discovery(candidate, prior_rows, demand_rows)
    evidence = evidence_index(evidence_items or [])
    grounded, problem, cells = verify_mapping(mapping, evidence, prior_rows)
    if not grounded:
        decision, reason = decide_discovery(candidate, prior_rows, demand_rows)
        return decision, reason + f" Functional mapping rejected: {problem}."
    capabilities = mapping["capabilities"]
    covered_by = _coverage(capabilities, cells, prior_rows, evidence)
    full = [alt for alt, ids in covered_by.items() if ids == {str(cap["id"]) for cap in capabilities}]
    if full:
        return "ALREADY_EXISTS", _exists_reason(full[0], prior_rows, capabilities, cells, evidence)
    gaps = _gaps(capabilities, cells, prior_rows, evidence)
    repo_id = str(candidate.get("repo_id") or "")
    terms = split_terms(candidate.get("core_terms"))
    passed = [row for row in demand_rows if demand_qualifies(row, terms, repo_id)]
    gap_text = " Gaps: " + "; ".join(gaps) + "."
    if not passed:
        return "NO_GO", "external demand for the problem was not shown." + gap_text + " " + FUNCTIONAL_METHOD
    demand = passed[0]
    return (
        "CANDIDATE",
        f"External demand is {demand.get('kind')} {demand.get('url')}." + gap_text + " " + FUNCTIONAL_METHOD,
    )


def _coverage(capabilities, cells, prior_rows, evidence) -> dict[str, set[str]]:
    wanted = {str(cap["id"]) for cap in capabilities}
    covered: dict[str, set[str]] = {}
    for cell in cells:
        if cell.get("status") != "covered":
            continue
        cap_id = str(cell.get("capability_id") or "")
        if cap_id not in wanted:
            continue
        row = _row_for_url(_norm_url(str(cell.get("alternative_url") or "")), prior_rows)
        if row is None or functional_maintenance_block(row, evidence):
            continue
        covered.setdefault(str(row.get("alternative_id") or ""), set()).add(cap_id)
    return covered


def _gaps(capabilities, cells, prior_rows, evidence) -> list[str]:
    lines = []
    for capability in capabilities:
        cap_id = str(capability["id"])
        related = [cell for cell in cells if str(cell.get("capability_id") or "") == cap_id]
        covered = []
        for cell in related:
            if cell.get("status") != "covered":
                continue
            row = _row_for_url(_norm_url(str(cell.get("alternative_url") or "")), prior_rows)
            if row and not functional_maintenance_block(row, evidence):
                covered.append(str(row.get("alternative_id") or ""))
        if covered:
            continue
        if not related:
            lines.append(f"{cap_id} has no mapping cell")
            continue
        bits = []
        for cell in related:
            row = _row_for_url(_norm_url(str(cell.get("alternative_url") or "")), prior_rows)
            name = str((row or {}).get("alternative_id") or cell.get("alternative_url") or "unknown")
            status = cell.get("status")
            if status == "covered" and row is not None:
                block = functional_maintenance_block(row, evidence)
                bits.append(f"{name} quote verified but {block}")
            else:
                bits.append(f"{name} is {status}")
        lines.append(f"{cap_id}: " + "; ".join(bits))
    return lines or ["no capability was covered"]


def _exists_reason(alternative_id: str, prior_rows, capabilities, cells, evidence) -> str:
    row = next(item for item in prior_rows if str(item.get("alternative_id") or "") == alternative_id)
    quotes = []
    for capability in capabilities:
        cap_id = str(capability["id"])
        match = next(
            cell
            for cell in cells
            if str(cell.get("capability_id") or "") == cap_id
            and cell.get("status") == "covered"
            and _row_for_url(_norm_url(str(cell.get("alternative_url") or "")), [row]) is not None
        )
        quotes.append(f"{cap_id} cited as {capability.get('citation')!r}; covered by {match.get('quote')!r}")
    return (
        f"{alternative_id} ({row.get('url')}) is a maintained free OSI alternative "
        f"({functional_licence(row, evidence)}, pushed {row.get('pushed_at')}, "
        f"{observed_days(row)} days before collection) and covers every core capability. "
        + " ".join(quotes)
        + " "
        + FUNCTIONAL_METHOD
    )


def _row_for_url(url: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not url:
        return None
    for row in rows:
        if _norm_url(str(row.get("url") or "")) == url:
            return row
    return None


def _norm_url(url: str) -> str:
    return url.strip().rstrip("/").casefold()
