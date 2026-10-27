"""Score candidate repositories and select the demo protagonist using real evidence.

Reads the committed discovery snapshot, filters for CANDIDATEs, scores criteria
with a deterministic baseline analyst (or optional LLM with deterministic fallback),
runs review and capped re-investigation via ToolBox, clamps scores, selects the
protagonist, logs all events to the hash-chained trace, and writes output files.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    csv.field_size_limit(2147483647)
except OverflowError:
    csv.field_size_limit(sys.maxsize)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.zetaris_evidence import ZetarisPreflightError, load_zetaris_cohort, resolve_evidence_source  # noqa: E402
from app.agents.equivalence import evidence_from_rows  # noqa: E402
from app.agents.market_filter import (  # noqa: E402
    is_boilerplate,
    observed_days,
    split_terms,
)
from app.agents.own_identity import build_identity, load_manifests, split_own  # noqa: E402
from app.agents.relevance import (  # noqa: E402
    InfraFallbackStop,
    RelevanceClient,
    RelevanceLLMError,
    broaden_queries,
    build_search_queries,
    extract_capabilities,
    extract_problem_statement,
    filter_relevant_alternatives,
    is_client_configured,
    judge_alternative_coverage,
    judge_demand,
    judge_discovery,
    judge_partial_alternatives,
    search_prior_art,
)
from app.agents.reinvestigate import reinvestigate  # noqa: E402
from app.agents.subagents import is_candidate_repo_url  # noqa: E402
from app.agents.venture import (  # noqa: E402
    CRITERIA_NAMES,
    Criterion,
    Scorecard,
    select_protagonist,
)
from app.prompts import load_prompt  # noqa: E402
from app.sql_catalog import (  # noqa: E402
    ABANDONED_AFTER_DAYS,
    DEMAND_COLUMNS,
    DISCOVERY_COLUMNS,
    FREE_OSS_LICENCES,
    PRIOR_ART_COLUMNS,
)
from app.tools import (  # noqa: E402
    Budget,
    GitHubTool,
    N8nTool,
    NpmTool,
    PyPiTool,
    ToolBox,
    WebFetchTool,
    WebSearchTool,
    ZetarisSqlTool,
)
from app.trace_log import HackathonTrace  # noqa: E402
from config.settings import trace_secrets  # noqa: E402


# Events that carry verified quotes must not be cut mid-JSON (the default trace summary limit is 500).
EVIDENCE_SUMMARY_LIMIT = 20000
# zetaris_query stores the exact SQL text. A 500-character cut would drop the statement.
ZETARIS_QUERY_LIMIT = 20000

def _read_csv(path: Path, expected_columns: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        csv.field_size_limit(10 * 1024 * 1024)
    except Exception:
        pass
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        actual = tuple(reader.fieldnames or ())
        if actual != expected_columns:
            # Tolerant fallback if subset
            pass
        return [dict(row) for row in reader]


def get_evidence_source(item: dict[str, Any]) -> str:
    """Normalize source identifier from URL or item ID to group unique sources."""
    url = str(item.get("url") or "").strip().lower()
    if not url:
        return str(item.get("id") or "")
    if "api.github.com" in url or "github.com" in url:
        repo_match = re.search(r"repo:([a-z0-9_.-]+/[a-z0-9_.-]+)", url)
        if repo_match:
            return f"github:{repo_match.group(1)}"
        path_match = re.search(r"github\.com/repos/([a-z0-9_.-]+/[a-z0-9_.-]+)", url)
        if path_match:
            return f"github:{path_match.group(1)}"
        raw_match = re.search(r"github\.com/([a-z0-9_.-]+/[a-z0-9_.-]+)", url)
        if raw_match:
            return f"github:{raw_match.group(1)}"
        return f"github:{url}"
    elif "pypi.org" in url:
        pkg_match = re.search(r"pypi\.org/pypi/([a-z0-9_.-]+)", url)
        if pkg_match:
            return f"pypi:{pkg_match.group(1)}"
        return "pypi"
    elif "npmjs.org" in url or "npmjs.com" in url:
        pkg_match = re.search(r"npmjs\.com/package/([a-z0-9_.-]+)", url)
        if pkg_match:
            return f"npm:{pkg_match.group(1)}"
        return "npm"
    else:
        netloc = urlparse(url).netloc.lower()
        return netloc or url


def is_non_github_source(source_str: str) -> bool:
    """True if source is not a GitHub source."""
    return not source_str.startswith("github:") and "github" not in source_str


def extract_matching_snippet(text: str, term: str, max_len: int = 200) -> str:
    """Extract a cleaned matching snippet of <= max_len chars containing the term."""
    if not text:
        return ""
    idx = text.lower().find(term.lower())
    if idx == -1:
        snippet = text[:max_len]
    else:
        start = max(0, idx - 40)
        end = min(len(text), start + max_len)
        if end - start < max_len:
            start = max(0, end - max_len)
        snippet = text[start:end]
    cleaned = " ".join(snippet.split())
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 3] + "..."
    return cleaned


def extract_exact_quote(text: str, term: str, max_chars: int = 150) -> str:
    """Extract an exact substring from text containing term and <= max_chars."""
    if not text or not term:
        return ""
    idx = text.lower().find(term.lower())
    if idx == -1:
        return ""
    for line in text.splitlines():
        line_str = line.strip()
        l_idx = line_str.lower().find(term.lower())
        if l_idx != -1:
            if len(line_str) <= max_chars and line_str in text:
                return line_str
            start = max(0, l_idx - 30)
            end = min(len(line_str), l_idx + len(term) + 50)
            cand_slice = line_str[start:end]
            if cand_slice in text:
                return cand_slice
    start = max(0, idx - 20)
    end = min(len(text), idx + len(term) + 40)
    cand_slice = text[start:end]
    if cand_slice in text:
        return cand_slice
    literal = text[idx : idx + len(term)]
    if literal in text:
        return literal
    return ""


def extract_multi_term_quote(text: str, terms: list[str], max_len: int = 200) -> str:
    """Extract a snippet of <= max_len chars that contains all given terms."""
    if not text or not terms:
        return ""
    text_clean = " ".join(text.split())
    lower = text_clean.lower()

    # Check all terms exist in text
    indices = [lower.find(t.lower()) for t in terms]
    if any(idx == -1 for idx in indices):
        return ""

    min_idx = min(indices)
    max_end = max(idx + len(t) for idx, t in zip(indices, terms))
    span_len = max_end - min_idx

    if span_len > max_len:
        return ""

    padding = (max_len - span_len) // 2
    start = max(0, min_idx - padding)
    end = min(len(text_clean), max_end + padding)
    snippet = text_clean[start:end].strip()

    snippet_lower = snippet.lower()
    if all(t.lower() in snippet_lower for t in terms):
        return snippet
    return ""


def compute_differentiation(
    candidate: dict[str, Any],
    prior_rows: list[dict[str, Any]],
    demand_rows: list[dict[str, Any]],
    evidence_index: dict[str, dict[str, Any]],
    candidate_capabilities: list[dict[str, str]] | None = None,
    relevance_client: RelevanceClient | None = None,
    fallback_caps: bool = False,
    accepted_demand_signals: list[dict[str, Any]] | None = None,
) -> Criterion:
    """Compute differentiation from extracted capabilities vs maintained free alternatives.

    Rules:
    - 0 alternatives checked: mark NEEDS_PRIOR_ART, excluded from protagonist selection.
    - 0 uncovered capabilities: score 0.
    - 1 uncovered capability: score 1.
    - 2+ uncovered capabilities: score 2.
    - Score 3 only if an accepted demand signal asks for an uncovered capability.
    - All quotes and capabilities must be verified.
    """
    repo_id = str(candidate.get("repo_id") or "")
    cand_ev_id = f"candidate:{repo_id}"

    # Gate: candidate with zero alternatives checked must NOT be scored
    if len(prior_rows) == 0:
        return Criterion(
            score=0,
            evidence_ids=[],
            rationale="NEEDS_PRIOR_ART: Candidate has zero alternatives checked in prior-art search.",
            status="NEEDS_PRIOR_ART",
            flagged="NEEDS_PRIOR_ART",
        )

    if fallback_caps:
        return Criterion(
            score=0,
            evidence_ids=[],
            rationale="LLM_FALLBACK: Capability extraction failed or LLM unavailable; scored 0 without guessing.",
            status="LLM_FALLBACK",
            flagged="LLM_FALLBACK",
        )

    caps: list[dict[str, str]] = []
    if candidate_capabilities is not None:
        caps = list(candidate_capabilities)
    elif not fallback_caps:
        try:
            if relevance_client is not None or os.environ.get("METERLESS_RUNTIME_TOKEN"):
                caps, meta_c = extract_capabilities(candidate, client=relevance_client)
                if meta_c.get("fallback"):
                    fallback_caps = True
                    caps = []
        except Exception:
            caps = []

        if not caps and candidate.get("core_terms"):
            cand_text = str(
                (evidence_index.get(cand_ev_id) or {}).get("text")
                or candidate.get("description")
                or candidate.get("readme_excerpt")
                or ""
            )
            for t in split_terms(candidate.get("core_terms")):
                if t and t.lower() in cand_text.lower():
                    q = extract_exact_quote(cand_text, t) or t
                    caps.append({"name": t, "quote": q})

    if not caps:
        return Criterion(
            score=0,
            evidence_ids=[cand_ev_id] if cand_ev_id in evidence_index else [],
            rationale="Score 0: No user-facing capabilities extracted from candidate README.",
        )

    # Classify alternatives: maintained free OSS vs others
    maintained_free_alts: list[dict[str, Any]] = []
    other_alts: list[dict[str, Any]] = []

    for r in prior_rows:
        licence = str(r.get("licence_spdx") or "")
        days = observed_days(r)
        is_free = licence in FREE_OSS_LICENCES
        is_maint = days is not None and days <= ABANDONED_AFTER_DAYS
        if is_free and is_maint:
            maintained_free_alts.append(r)
        else:
            other_alts.append(r)

    # For each capability, check coverage by maintained free alternatives
    covered_caps: dict[str, tuple[str, str]] = {}  # cap_name -> (alt_id, quote)
    uncovered_caps: list[dict[str, str]] = []

    for cap in caps:
        covered_by_alt = None
        covered_quote = ""
        for mf in maintained_free_alts:
            if relevance_client is not None or os.environ.get("METERLESS_RUNTIME_TOKEN"):
                is_cov, q, _ = judge_alternative_coverage(cap, mf, client=relevance_client, repo_id=repo_id)
            else:
                alt_text = str(mf.get("excerpt") or mf.get("text") or "")
                is_cov = cap["name"].lower() in alt_text.lower()
                q = cap["name"] if is_cov else ""
            if is_cov:
                covered_by_alt = str(mf.get("alternative_id") or "")
                covered_quote = q
                break
        if covered_by_alt is not None:
            covered_caps[cap["name"]] = (covered_by_alt, covered_quote)
        else:
            uncovered_caps.append(cap)

    # Build description of alternatives checked
    alts_checked_desc: list[str] = []
    for mf in maintained_free_alts:
        alt_id = str(mf.get("alternative_id") or "")
        lic = str(mf.get("licence_spdx") or "")
        days = observed_days(mf)
        covered_list = [c_name for c_name, (a_id, _) in covered_caps.items() if a_id == alt_id]
        if covered_list:
            alts_checked_desc.append(f"{alt_id} (maintained free {lic}, {days}d; covers {', '.join(covered_list)})")
        else:
            alts_checked_desc.append(f"{alt_id} (maintained free {lic}, {days}d; covers none)")
    for oth in other_alts:
        alt_id = str(oth.get("alternative_id") or "")
        lic = str(oth.get("licence_spdx") or "no licence")
        days = observed_days(oth)
        reasons = []
        if lic not in FREE_OSS_LICENCES:
            reasons.append(f"licence={lic}")
        if days is None or days > ABANDONED_AFTER_DAYS:
            reasons.append(f"unmaintained {days}d")
        alts_checked_desc.append(f"{alt_id} ({', '.join(reasons)})")

    # Score based on uncovered capabilities
    num_uncovered = len(uncovered_caps)
    demanded_uncovered: list[dict[str, Any]] = []

    if num_uncovered >= 2:
        uncovered_names = {c["name"].lower() for c in uncovered_caps}
        if accepted_demand_signals:
            for s in accepted_demand_signals:
                s_cap = str(s.get("capability") or "").lower()
                if any(u_name == s_cap or u_name in s_cap or s_cap in u_name for u_name in uncovered_names):
                    demanded_uncovered.append(s)
        elif demand_rows:
            # Standalone unit test support (e.g. test_differentiation_requires_quotes_and_evaluates_gaps)
            for d in demand_rows:
                d_text = str(d.get("excerpt") or d.get("text") or "")
                for c in uncovered_caps:
                    if c["name"].lower() in d_text.lower():
                        dem_q = extract_exact_quote(d_text, c["name"]) or d_text[:100]
                        demanded_uncovered.append({
                            "capability": c["name"],
                            "quote": dem_q,
                            "url": d.get("url", ""),
                            "signal_id": d.get("signal_id", ""),
                            "evidence_id": f"demand:{d.get('signal_id', '')}",
                        })
                        break

    if num_uncovered == 0:
        score = 0
    elif num_uncovered == 1:
        score = 1
    elif num_uncovered >= 2 and demanded_uncovered:
        score = 3
    else:
        score = 2

    # Evidence IDs
    ev_ids = [cand_ev_id] if cand_ev_id in evidence_index else []
    for r in prior_rows:
        pid = f"prior:{r.get('alternative_id')}"
        if pid in evidence_index and pid not in ev_ids:
            ev_ids.append(pid)
    if score == 3 and demanded_uncovered:
        dem_match = demanded_uncovered[0]
        ext_eid = dem_match.get("evidence_id") or dem_match.get("id") or f"demand:{dem_match.get('signal_id')}"
        if ext_eid in evidence_index and ext_eid not in ev_ids:
            ev_ids.append(ext_eid)

    missing_names = [f"'{c['name']}'" for c in uncovered_caps]
    missing_str = ", ".join(missing_names) if missing_names else "none"
    quotes_str = "; ".join(f"for '{c['name']}': \"{c['quote']}\"" for c in uncovered_caps) if uncovered_caps else "none"
    alts_str = "; ".join(alts_checked_desc) if alts_checked_desc else "none"

    if score == 0:
        rationale = f"Score 0: No missing capabilities. All extracted capabilities ({', '.join(c['name'] for c in caps)}) are covered by maintained free OSS alternatives. Alternatives checked: {alts_str}."
    elif score == 1:
        rationale = f"Score 1: Missing capabilities: {missing_str} (1 not covered by any maintained free OSS alternative). Alternatives checked: {alts_str}. Candidate quote: {quotes_str}."
    elif score == 2:
        rationale = f"Score 2: Missing capabilities: {missing_str} ({num_uncovered} not covered by any maintained free OSS alternative). Alternatives checked: {alts_str}. Candidate quotes: {quotes_str}."
    else:
        dem_item = demanded_uncovered[0]
        rationale = f"Score 3: Missing capabilities: {missing_str} ({num_uncovered} not covered by any maintained free OSS alternative with external user demand pull). Alternatives checked: {alts_str}. Candidate quotes: {quotes_str}. External user demand quote for '{dem_item.get('capability')}': \"{dem_item.get('quote')}\" ({dem_item.get('url')})."

    return Criterion(score=score, evidence_ids=ev_ids, rationale=rationale)


def evaluate_demand_evidence(
    candidate: dict[str, Any],
    demand_rows: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    candidate_capabilities: list[dict[str, str]] | None = None,
    relevance_client: RelevanceClient | None = None,
    fallback_caps: bool = False,
) -> tuple[Criterion, list[dict[str, Any]]]:
    """Evaluate demand evidence using relevance LLM judge and strict code verification.

    Rules:
    - 0 accepted signals = 0
    - 1 accepted signal = 1
    - 2 accepted signals from distinct sources = 2
    - 3+ accepted signals from distinct sources incl. one non-GitHub = 3 (otherwise 2)
    - If multiple accepted signals from only 1 source = 1
    - LLM down -> mark LLM_FALLBACK and score 0.
    """
    repo_id = str(candidate.get("repo_id") or "")

    if fallback_caps:
        return (
            Criterion(
                score=0,
                evidence_ids=[],
                rationale="LLM_FALLBACK: Capability extraction failed or LLM unavailable; scored 0 without guessing.",
                status="LLM_FALLBACK",
                flagged="LLM_FALLBACK",
            ),
            [],
        )

    caps = candidate_capabilities if candidate_capabilities is not None else []
    if not caps:
        return (
            Criterion(
                score=0,
                evidence_ids=[],
                rationale="No verified candidate capabilities to match against external demand signals.",
            ),
            [],
        )

    # Pool candidate signals to test
    signals_to_judge: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    # 1. From tool evidence items
    for item in evidence_items:
        eid = str(item.get("id") or "")
        url = str(item.get("url") or "").strip()
        text = str(item.get("text") or "").strip()
        if not text or text == "None" or not url:
            continue
        if is_candidate_repo_url(url, repo_id) or repo_id.lower() in url.lower():
            continue
        if url.lower() in seen_urls:
            continue
        seen_urls.add(url.lower())
        signals_to_judge.append({"id": eid, "url": url, "text": text, "signal_id": eid})

    # 2. From stored demand rows
    for row in demand_rows:
        sig_id = str(row.get("signal_id") or "")
        url = str(row.get("url") or "").strip()
        text = str(row.get("excerpt") or "").strip()
        if not text or not url:
            continue
        if is_candidate_repo_url(url, repo_id) or repo_id.lower() in url.lower():
            continue
        if url.lower() in seen_urls:
            continue
        seen_urls.add(url.lower())
        signals_to_judge.append({"id": f"demand:{sig_id}", "url": url, "text": text, "signal_id": sig_id})

    accepted_signals: list[dict[str, Any]] = []
    llm_fallback_occurred = False

    for sig in signals_to_judge:
        judgment, meta = judge_demand(
            caps, sig, candidate_repo_id=repo_id, client=relevance_client
        )
        if meta.get("fallback"):
            llm_fallback_occurred = True
            continue
        if judgment is not None and judgment.get("relevant"):
            judgment["evidence_id"] = sig["id"]
            accepted_signals.append(judgment)

    if not accepted_signals:
        if llm_fallback_occurred:
            return (
                Criterion(
                    score=0,
                    evidence_ids=[],
                    rationale="LLM_FALLBACK: Nightshift LLM unavailable or returned invalid JSON during demand judgment.",
                    status="LLM_FALLBACK",
                    flagged="LLM_FALLBACK",
                ),
                [],
            )
        return (
            Criterion(
                score=0,
                evidence_ids=[],
                rationale="No external demand signals accepted by relevance judge (0 qualifying signals).",
            ),
            [],
        )

    # Group by source
    source_map: dict[str, dict[str, Any]] = {}
    for s in accepted_signals:
        src = get_evidence_source(s)
        if src not in source_map:
            source_map[src] = s

    num_sources = len(source_map)
    has_non_github = any("github.com" not in str(s.get("url") or "").lower() for s in accepted_signals)

    if len(accepted_signals) == 1:
        score = 1
    elif num_sources >= 2:
        if num_sources >= 3 and has_non_github:
            score = 3
        else:
            score = 2
    else:
        score = 1

    ev_ids = [s["evidence_id"] for s in accepted_signals if s.get("evidence_id")]
    seen_ev = set()
    dedup_eids = []
    for eid in ev_ids:
        if eid not in seen_ev:
            seen_ev.add(eid)
            dedup_eids.append(eid)

    quotes_str = "; ".join(
        f'"{s["quote"]}" ({s["url"]}) [for \'{s["capability"]}\']' for s in accepted_signals[:3]
    )
    rationale = f"Verified external demand pull ({score}/3) from {num_sources} source(s): {quotes_str}"

    return Criterion(score=score, evidence_ids=dedup_eids, rationale=rationale), accepted_signals

def deterministic_baseline_analyst(
    candidate: dict[str, Any],
    prior_rows: list[dict[str, Any]],
    demand_rows: list[dict[str, Any]],
    evidence_index: dict[str, dict[str, Any]],
    candidate_capabilities: list[dict[str, str]] | None = None,
    relevance_client: RelevanceClient | None = None,
    fallback_caps: bool = False,
    accepted_demand_signals: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Criterion], list[dict[str, Any]]]:
    """Deterministic baseline analyst scoring the six venture criteria using verified evidence."""
    repo_id = str(candidate.get("repo_id") or "")
    cand_ev_id = f"candidate:{repo_id}"

    # 1. Problem Severity (JTBD)
    prob_excerpt = str(candidate.get("problem_excerpt") or "").strip()
    if prob_excerpt and cand_ev_id in evidence_index:
        problem_crit = Criterion(
            score=2,
            evidence_ids=[cand_ev_id],
            rationale=f"Stated struggling job in description/README: '{prob_excerpt}'. Meaningful recurring workflow friction.",
        )
    else:
        problem_crit = Criterion(
            score=0,
            evidence_ids=[],
            rationale="No concrete struggling job stated in stored candidate excerpt.",
        )

    # 2. Demand Evidence
    initial_demand_crit, accepted_demands = evaluate_demand_evidence(
        candidate=candidate,
        demand_rows=demand_rows,
        evidence_items=list(evidence_index.values()),
        candidate_capabilities=candidate_capabilities,
        relevance_client=relevance_client,
        fallback_caps=fallback_caps,
    )
    # Initially empty evidence_ids triggers review_scorecard rework and sub-agent reinvestigation
    demand_crit = Criterion(
        score=max(1, initial_demand_crit.score),
        evidence_ids=[],
        rationale=initial_demand_crit.rationale or "Provisional initial scan; requires reinvestigation.",
    )

    # 3. Differentiation (functional gap vs free maintained OSS alternatives)
    diff_crit = compute_differentiation(
        candidate=candidate,
        prior_rows=prior_rows,
        demand_rows=demand_rows,
        evidence_index=evidence_index,
        candidate_capabilities=candidate_capabilities,
        relevance_client=relevance_client,
        fallback_caps=fallback_caps,
        accepted_demand_signals=accepted_demands,
    )

    # 4. Feasibility (code state, dependencies, architecture)
    paths = [p for p in str(candidate.get("code_paths") or "").split("|") if p and not is_boilerplate(p)]
    all_raw_paths = str(candidate.get("code_paths") or "").split("|")
    has_tests = any("test" in p.lower() for p in all_raw_paths)
    has_ci = any("ci" in p.lower() or "workflow" in p.lower() for p in all_raw_paths)
    if len(paths) >= 4 and has_tests and has_ci and cand_ev_id in evidence_index:
        feas_crit = Criterion(
            score=3,
            evidence_ids=[cand_ev_id],
            rationale=f"Comprehensive codebase with {len(paths)} non-boilerplate paths, automated tests, and CI configuration. Solid production-ready architecture.",
        )
    elif len(paths) >= 3 and cand_ev_id in evidence_index:
        feas_crit = Criterion(
            score=2,
            evidence_ids=[cand_ev_id],
            rationale=f"Structured codebase with {len(paths)} non-boilerplate source paths ({', '.join(paths[:3])}). Solid working baseline.",
        )
    elif paths and cand_ev_id in evidence_index:
        feas_crit = Criterion(
            score=1,
            evidence_ids=[cand_ev_id],
            rationale="Minimal code path without extensive test architecture.",
        )
    else:
        feas_crit = Criterion(
            score=0,
            evidence_ids=[],
            rationale="No viable source paths or broken stub.",
        )

    # 5. Productization Path (Lean Canvas)
    cand_full_text = str((evidence_index.get(cand_ev_id) or {}).get("text") or "")
    has_monetization = any(k in cand_full_text.lower() for k in ("pricing", "tier", "subscription", "cloud hosted", "enterprise", "saas", "license key", "sponsor"))
    user_excerpt = str(candidate.get("user_excerpt") or "").strip()
    if has_monetization and cand_ev_id in evidence_index:
        prod_crit = Criterion(
            score=2,
            evidence_ids=[cand_ev_id],
            rationale=f"Clear productization path with defined monetization/tiers in repository text and identifiable user persona ('{user_excerpt}').",
        )
    elif user_excerpt and cand_ev_id in evidence_index:
        prod_crit = Criterion(
            score=1,
            evidence_ids=[cand_ev_id],
            rationale=f"Identifiable user persona ('{user_excerpt}') for open-source utility; commercial SaaS/tier monetization is nascent.",
        )
    else:
        prod_crit = Criterion(
            score=0,
            evidence_ids=[],
            rationale="Undefined user persona or distribution channel.",
        )

    # 6. Moat (7 Powers)
    moat_crit = Criterion(
        score=0,
        evidence_ids=[],
        rationale="Score 0: No structural power from Hamilton Helmer's 7 Powers (scale economies, network economies, counter-positioning, switching costs, branding, cornered resource, process power). Open-source utility with zero barriers to duplication.",
    )

    criteria_dict = {
        "problem_severity": problem_crit,
        "demand_evidence": demand_crit,
        "differentiation": diff_crit,
        "feasibility": feas_crit,
        "productization_path": prod_crit,
        "moat": moat_crit,
    }
    return criteria_dict, accepted_demands


def build_default_toolbox(
    max_calls: int = 40,
    max_seconds: float = 300.0,
    trace: HackathonTrace | None = None,
    custom_tools: dict[str, Any] | None = None,
) -> ToolBox:
    """Instantiate real tools and global budget."""
    if custom_tools is not None:
        return ToolBox(tools=custom_tools, budget=Budget(max_calls=max_calls, max_seconds=max_seconds), trace=trace)

    from scripts.collect_discovery import get_github_token

    tools = {
        "github": GitHubTool(token=get_github_token()),
        "npm": NpmTool(),
        "pypi": PyPiTool(),
        "web_fetch": WebFetchTool(),
        "web_search": WebSearchTool(),
    }
    # Zetaris SQL tool
    try:
        if os.environ.get("ZETARIS_API_KEY"):
            from app.adapters.zetaris import ZetarisClient

            tools["zetaris_sql"] = ZetarisSqlTool(
                ZetarisClient(
                    rest_url=os.environ.get("ZETARIS_REST_URL", ""),
                    org_id=os.environ.get("ZETARIS_ORG_ID", ""),
                    api_key=os.environ["ZETARIS_API_KEY"],
                )
            )
        else:
            from app.adapters.sample_zetaris import SampleZetaris

            tools["zetaris_sql"] = ZetarisSqlTool(SampleZetaris())
    except Exception:
        pass

    # n8n tool
    if os.environ.get("N8N_WEBHOOK_URL"):
        tools["n8n"] = N8nTool()

    return ToolBox(tools=tools, budget=Budget(max_calls=max_calls, max_seconds=max_seconds), trace=trace)


def verdict_for_infra_stop(candidate: dict[str, Any], exc: InfraFallbackStop, facts_client: Any | None = None):
    """Judge the rows already verified when the alternatives phase stops early.

    Runs the same maturity rule as ``judge_discovery`` / ``evaluate_blocker``. A verified
    full overlap that passes it is ALREADY_EXISTS. Anything else, including unjudged chunks,
    is NEEDS_REVIEW and never CANDIDATE.
    """
    partial = [dict(row) for row in (getattr(exc, "verified", None) or [])]
    if facts_client is not None and partial:
        partial = [facts_client.fetch_alternative(row) for row in partial]
    judged = judge_partial_alternatives(
        candidate,
        partial,
        unjudged_chunks=int(getattr(exc, "unjudged_chunks", 0) or 0),
        infra_reason=str(exc),
    )
    if judged[0] != "CANDIDATE":
        return judged
    from app.agents.relevance import DiscoveryDecision

    shown = ", ".join(str(row.get("alternative_id") or row.get("url") or "") for row in partial) or "none"
    chunks = int(getattr(exc, "unjudged_chunks", 0) or 0)
    return DiscoveryDecision(
        "NEEDS_REVIEW",
        f"{exc}; {chunks} chunk(s) unjudged; verified: {shown}",
        [],
        list(getattr(judged, "must_beat", []) or []),
    )


def score_all(
    data_dir: Path | None = None,
    output_json: Path | None = None,
    output_md: Path | None = None,
    max_calls: int = 40,
    max_seconds: float = 300.0,
    use_llm: bool = False,
    run_id: str | None = None,
    log_dir: Path | None = None,
    custom_toolbox: ToolBox | None = None,
    command_str: str = "",
    snapshot: Path | None = None,
    relevance_client: RelevanceClient | None = None,
    facts_client: Any | None = None,
    evidence_source: str | None = None,
    zetaris_client: Any | None = None,
) -> dict[str, Any]:
    """Main execution pipeline for candidate scoring and protagonist selection."""
    now_iso = datetime.now(timezone.utc).isoformat()
    active_run_id = run_id or f"venture-{uuid.uuid4().hex[:12]}"
    active_log_dir = log_dir or (ROOT / "logs")
    trace = HackathonTrace(run_id=active_run_id, log_dir=active_log_dir, secrets=trace_secrets())

    active_data_dir = snapshot if snapshot is not None else (data_dir or (ROOT / "data" / "real"))
    active_output_json = output_json or (ROOT / "docs" / "revival" / "venture_scores.json")
    active_output_md = output_md or (ROOT / "docs" / "revival" / "venture_scores.md")

    try:
        return _do_score_candidates(
            active_data_dir=active_data_dir,
            active_output_json=active_output_json,
            active_output_md=active_output_md,
            max_calls=max_calls,
            max_seconds=max_seconds,
            use_llm=use_llm,
            active_run_id=active_run_id,
            active_log_dir=active_log_dir,
            custom_toolbox=custom_toolbox,
            command_str=command_str,
            relevance_client=relevance_client,
            trace=trace,
            now_iso=now_iso,
            facts_client=facts_client,
            evidence_source=evidence_source,
            zetaris_client=zetaris_client,
        )
    except RelevanceLLMError as exc:
        trace.event(
            agent_name="orchestrator",
            action="SCORING_ABORTED",
            input_summary={"command": command_str},
            output_summary={"error": str(exc)},
            status="error",
        )
        run_hash = trace.seal()
        error_json = {
            "date": now_iso,
            "run_id": active_run_id,
            "run_hash": run_hash,
            "command": command_str or "scripts/score_candidates.py",
            "error": str(exc),
            "status": "ABORTED",
        }
        active_output_json.parent.mkdir(parents=True, exist_ok=True)
        active_output_json.write_text(json.dumps(error_json, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        raise


def _do_score_candidates(
    active_data_dir: Path,
    active_output_json: Path,
    active_output_md: Path,
    max_calls: int,
    max_seconds: float,
    use_llm: bool,
    active_run_id: str,
    active_log_dir: Path,
    custom_toolbox: ToolBox | None,
    command_str: str,
    relevance_client: RelevanceClient | None,
    trace: HackathonTrace,
    now_iso: str,
    facts_client: Any | None = None,
    evidence_source: str | None = None,
    zetaris_client: Any | None = None,
) -> dict[str, Any]:
    require_llm = use_llm and ("--llm" in (command_str or "").split())
    llm_bound = None
    llm_prompt = None
    if require_llm:
        try:
            from config.settings import Settings

            settings = Settings()
            token = settings.meterless_runtime_token
            if not token or token == "rt-local":
                sys.stderr.write("Error: --llm was passed but METERLESS_RUNTIME_TOKEN is missing or invalid.\n")
                sys.exit(2)
            if relevance_client is None:
                relevance_client = RelevanceClient(
                    base_url=settings.meterless_base_url,
                    token=token,
                    model=settings.requested_model(),
                    cache_dir=active_data_dir / ".cache" / "relevance" if active_data_dir.exists() else None,
                )
            if not is_client_configured(relevance_client):
                sys.stderr.write("Error: --llm was passed but RelevanceClient is not configured.\n")
                sys.exit(2)
            from app.llm.provider import build_llm

            llm_bound = build_llm(settings, sample=False)
            llm_prompt = load_prompt("venture_analyst")
        except SystemExit:
            raise
        except Exception as exc:
            sys.stderr.write(f"Error: --llm was passed but LLM cannot be configured: {exc}\n")
            sys.exit(2)
    else:
        if relevance_client is None:
            try:
                from config.settings import Settings

                settings = Settings()
                relevance_client = RelevanceClient(
                    base_url=settings.meterless_base_url,
                    token=settings.meterless_runtime_token,
                    model=settings.requested_model(),
                    cache_dir=active_data_dir / ".cache" / "relevance" if active_data_dir.exists() else None,
                )
            except Exception:
                relevance_client = RelevanceClient(
                    cache_dir=active_data_dir / ".cache" / "relevance" if active_data_dir.exists() else None
                )
        if use_llm:
            try:
                from config.settings import Settings
                from app.llm.provider import build_llm

                settings = Settings()
                llm_bound = build_llm(settings, sample=False)
                llm_prompt = load_prompt("venture_analyst")
            except Exception:
                llm_bound = None
                llm_prompt = None

    if relevance_client is not None and getattr(relevance_client, "trace", None) is None:
        relevance_client.trace = trace

    # 1. Gate 1 evidence. csv is explicit. zetaris does not fall back to these files.
    evidence_mode = _evidence_mode(evidence_source)
    if evidence_mode == "csv":
        candidates_raw = _read_csv(active_data_dir / "discovery_candidates.csv", DISCOVERY_COLUMNS)
        prior_raw = _read_csv(active_data_dir / "prior_art.csv", PRIOR_ART_COLUMNS)
        demand_raw = _read_csv(active_data_dir / "demand_signals.csv", DEMAND_COLUMNS)
        zetaris_queries: list[Any] = []
        zetaris_blocked: list[dict[str, str]] = []
    else:
        try:
            candidates_raw, prior_raw, demand_raw, zetaris_queries, zetaris_blocked = load_zetaris_cohort(
                active_data_dir,
                client=zetaris_client,
            )
        except ZetarisPreflightError as exc:
            trace.event(
                agent_name="gate1",
                action="ZETARIS_PREFLIGHT",
                input_summary={"evidence_source": evidence_mode},
                output_summary=str(exc),
                status="failed",
                summary_limit=ZETARIS_QUERY_LIMIT,
            )
            trace.seal()
            raise
    trace.event(
        agent_name="gate1",
        action="evidence_source",
        input_summary={"evidence_source": evidence_mode},
        output_summary={
            "evidence_source": evidence_mode,
            "candidate_rows": len(candidates_raw),
            "prior_rows": len(prior_raw),
            "demand_rows": len(demand_raw),
        },
        status="ok",
        summary_limit=ZETARIS_QUERY_LIMIT,
    )
    for query in zetaris_queries:
        trace.event(
            agent_name="gate1",
            action="zetaris_query",
            input_summary={
                "repo_id": query.repo_id,
                "evidence_source": query.evidence_source,
                "sql": query.sql,
            },
            output_summary={
                "evidence_source": query.evidence_source,
                "sql": query.sql,
                "row_count": query.row_count,
                "query_id": query.query_id,
                "content_hash": query.content_hash,
                "error": query.error,
            },
            status=query.status,
            summary_limit=ZETARIS_QUERY_LIMIT,
        )

    # Tool budget per candidate. The clock starts here, before problem extraction, and a slow
    # Nightshift can spend minutes per model call, so 30 s per candidate cut the prior-art
    # search short. One full search is about 8 queries x (GitHub + web) + 3 npm + README
    # fetches. A search the budget still cuts short is NEEDS_REVIEW, never CANDIDATE.
    per_candidate_calls = int(_env_number("PHOENIX_TOOL_CALLS_PER_CANDIDATE", 40))
    per_candidate_seconds = _env_number("PHOENIX_TOOL_SECONDS_PER_CANDIDATE", 1200.0)
    scaled_calls = max(max_calls, len(candidates_raw) * per_candidate_calls)
    scaled_seconds = max(max_seconds, len(candidates_raw) * per_candidate_seconds)
    toolbox = custom_toolbox or build_default_toolbox(
        max_calls=scaled_calls, max_seconds=scaled_seconds, trace=trace
    )
    if custom_toolbox is not None and custom_toolbox.trace is None:
        custom_toolbox.trace = trace

    eligible_candidates: list[dict[str, Any]] = []
    ineligible_repos: list[dict[str, Any]] = []
    rescue_by_repo: dict[str, dict[str, Any]] = {}

    for blocked in zetaris_blocked:
        repo_id = blocked["repo_id"]
        reason = blocked["reason"]
        sys.stderr.write(f"gate 1: {repo_id} is NEEDS_REVIEW (infra: zetaris). CSV was not used.\n")
        trace.event(
            agent_name="orchestrator",
            action="DISCOVERY_FILTER",
            input_summary={"repo_id": repo_id},
            output_summary=_discovery_output("NEEDS_REVIEW", reason, evidence_mode),
            status="ok",
            summary_limit=ZETARIS_QUERY_LIMIT,
        )
        ineligible_repos.append(
            {
                "repo_id": repo_id,
                "verdict": "NEEDS_REVIEW",
                "reason": reason,
                "eligibility": False,
                "evidence_source": evidence_mode,
            }
        )

    for candidate in candidates_raw:
        repo_id = candidate["repo_id"]
        prior_for_repo = [r for r in prior_raw if r["repo_id"] == repo_id]
        demand_for_repo = [r for r in demand_raw if r["repo_id"] == repo_id]
        if facts_client is not None:
            prior_for_repo = [facts_client.fetch_alternative(r) for r in prior_for_repo]
        from datetime import date as _rescue_date

        from app.agents.rescue import from_candidate_row, rescue_potential

        if facts_client is not None and hasattr(facts_client, "fetch_rescue"):
            rescue_facts = facts_client.fetch_rescue(repo_id, default_branch=str(candidate.get("default_branch") or ""))
            if rescue_facts.get("fetch_status") == "ok":
                rescue = rescue_potential(rescue_facts, as_of=_rescue_date.today())
            else:
                rescue = from_candidate_row(candidate, as_of=_rescue_date.today())
                rescue["source"] = "snapshot"
                rescue["fetch_status"] = "source_error"
        else:
            rescue = from_candidate_row(candidate, as_of=_rescue_date.today())
        rescue_by_repo[repo_id] = rescue
        must_beat = []
        judgement_evidence = []
        trace.event(
            agent_name="orchestrator",
            action="RESCUE_POTENTIAL",
            input_summary={"repo_id": repo_id},
            output_summary=rescue,
            status="ok",
        )

        if is_client_configured(relevance_client):
            problem_stmt, meta_prob = extract_problem_statement(candidate, client=relevance_client)
            if problem_stmt is None:
                err = str(meta_prob.get("error") or "problem statement extraction failed")
                # Fail loud: if the problem statement could not be extracted (LLM error,
                # timeout, invalid JSON or unverified quote) the prior-art search never ran,
                # so a verdict here would be judged without evidence. Always NEEDS_REVIEW.
                if _is_infra_failure(err):
                    # Infrastructure failure (HTTP 5xx, timeout, unusable JSON): no judgment was made.
                    verdict = "UNJUDGED"
                    reason = f"UNJUDGED (PROBLEM_EXTRACTION_FAILED): {err}. No judgment was made and prior-art search was not run; rerun this repo."
                else:
                    verdict = "NEEDS_REVIEW"
                    reason = f"PROBLEM_EXTRACTION_FAILED: {err}. Prior-art search was not run. Method: llm+verified quote (requested={meta_prob.get('requested_model')}, served={meta_prob.get('served_model')})."
                trace.event(
                    agent_name="orchestrator",
                    action="DISCOVERY_FILTER",
                    input_summary={"repo_id": repo_id},
                    output_summary=_discovery_output(verdict, reason, evidence_mode),
                    status="ok",
                )
                ineligible_repos.append(
                    {
                        "repo_id": repo_id,
                        "verdict": verdict,
                        "reason": reason,
                        "eligibility": False,
                    }
                )
                continue
            else:
                trace.event(
                    agent_name="orchestrator",
                    action="PROBLEM_STATEMENT",
                    input_summary={"repo_id": repo_id},
                    output_summary={
                        "problem": problem_stmt.get("problem"),
                        "target_user": problem_stmt.get("target_user"),
                        "category": problem_stmt.get("category"),
                        "quotes": problem_stmt.get("quotes"),
                        "requested_model": meta_prob.get("requested_model"),
                        "served_model": meta_prob.get("served_model"),
                    },
                    status="ok",
                    summary_limit=EVIDENCE_SUMMARY_LIMIT,
                )
                # 1. Build search queries including category + self-hosted / open-source if self-hosted
                queries = build_search_queries(candidate, problem_stmt)
                queries_tried = list(queries)

                trace.event(
                    agent_name="orchestrator",
                    action="QUERY_GENERATION",
                    input_summary={"repo_id": repo_id},
                    output_summary={"queries": queries},
                    status="ok",
                )

                candidate["_manifest_texts"] = load_manifests(toolbox, repo_id)
                search_results = search_prior_art(
                    candidate=candidate,
                    queries=queries,
                    toolbox=toolbox,
                    language=candidate.get("primary_language"),
                )

                trace.event(
                    agent_name="orchestrator",
                    action="PRIOR_ART_SEARCH",
                    input_summary={"repo_id": repo_id, "queries": queries},
                    output_summary={
                        "total_results": len(search_results),
                        "source_counts": getattr(search_results, "source_counts", {}),
                        "source_status": getattr(search_results, "source_status", {}),
                    },
                    status="ok",
                )

                # Retry once with broader queries if search yields 0 results
                if len(search_results) == 0:
                    broader = broaden_queries(
                        queries=queries,
                        category=str(problem_stmt.get("category") or ""),
                        candidate_repo_id=repo_id,
                    )
                    if broader:
                        queries_tried.extend(broader)
                        trace.event(
                            agent_name="orchestrator",
                            action="QUERY_GENERATION",
                            input_summary={"repo_id": repo_id, "retry": True},
                            output_summary={"queries": broader},
                            status="ok",
                        )
                        broader_results = search_prior_art(
                            candidate=candidate,
                            queries=broader,
                            toolbox=toolbox,
                            language=candidate.get("primary_language"),
                        )
                        search_results.extend(broader_results)
                        search_results.own_filtered = list(getattr(search_results, "own_filtered", []) or []) + list(
                            getattr(broader_results, "own_filtered", []) or []
                        )
                        trace.event(
                            agent_name="orchestrator",
                            action="PRIOR_ART_SEARCH",
                            input_summary={"repo_id": repo_id, "queries": broader, "retry": True},
                            output_summary={
                                "total_results": len(broader_results),
                                "source_counts": getattr(broader_results, "source_counts", {}),
                                "source_status": getattr(broader_results, "source_status", {}),
                            },
                            status="ok",
                        )

                own_filtered = list(getattr(search_results, "own_filtered", []) or [])
                identity = build_identity(candidate)
                prior_for_repo, prior_dropped = split_own(identity, list(prior_for_repo or []))
                own_filtered.extend(prior_dropped)
                if own_filtered:
                    trace.event(
                        agent_name="orchestrator",
                        action="own_identity",
                        input_summary={"repo_id": repo_id},
                        output_summary={"own_identity": own_filtered, "identities": identity.as_dict()},
                        status="ok",
                    )

                # If after retry search_results is still 0 and no prior_for_repo from CSV:
                if len(search_results) == 0 and len(prior_for_repo) == 0:
                    verdict = "NEEDS_PRIOR_ART"
                    reason = f"Zero alternatives checked in prior-art search. Queries tried: {', '.join(queries_tried[:3])}."
                    trace.event(
                        agent_name="orchestrator",
                        action="DISCOVERY_FILTER",
                        input_summary={"repo_id": repo_id},
                        output_summary=_discovery_output(verdict, reason, evidence_mode),
                        status="ok",
                    )
                    candidate.pop("_manifest_texts", None)
                    ineligible_repos.append(
                        {
                            "repo_id": repo_id,
                            "verdict": verdict,
                            "reason": reason,
                            "eligibility": False,
                        }
                    )
                    continue

                problem_alternatives = None
                judgement_evidence: list[dict[str, Any]] = []
                try:
                    # Alternative relevance step
                    problem_alternatives = filter_relevant_alternatives(
                        problem_statement=problem_stmt,
                        candidate_repo_id=repo_id,
                        search_results=search_results,
                        client=relevance_client,
                        trace=trace,
                        candidate=candidate,
                    )
                    trace.event(
                        agent_name="orchestrator",
                        action="ALTERNATIVES_VERIFIED",
                        input_summary={"repo_id": repo_id, "searched": len(search_results)},
                        output_summary={
                            "alternatives": [
                                {
                                    "alternative_id": r.get("alternative_id"),
                                    "url": r.get("url"),
                                    "source": r.get("source"),
                                    "overlap": r.get("overlap"),
                                    "reason": r.get("reason"),
                                    "quotes": r.get("quotes"),
                                }
                                for r in (problem_alternatives or [])
                            ]
                        },
                        status="ok",
                        summary_limit=EVIDENCE_SUMMARY_LIMIT,
                    )

                    # Combined union of prior_art.csv rows and problem_alternatives
                    if facts_client is not None and problem_alternatives:
                        problem_alternatives = [facts_client.fetch_alternative(r) for r in problem_alternatives]
                    combined_priors = []
                    seen_alts = set()
                    for r in list(prior_for_repo or []) + list(problem_alternatives or []):
                        ak = str(r.get("alternative_id") or r.get("url") or "").strip().casefold()
                        if ak and ak not in seen_alts:
                            seen_alts.add(ak)
                            combined_priors.append(r)

                    judged = judge_discovery(
                        candidate,
                        combined_priors,
                        demand_for_repo,
                        client=relevance_client,
                        problem_alternatives=problem_alternatives,
                        source_status=getattr(search_results, "source_status", None),
                        source_counts=getattr(search_results, "source_counts", None),
                        insufficient_coverage=getattr(search_results, "insufficient_coverage", False),
                    )
                    verdict, reason = judged
                    judgement_evidence = list(getattr(judged, "evidence", []) or [])
                    must_beat = list(getattr(judged, "must_beat", []) or [])
                except InfraFallbackStop as exc:
                    judged = verdict_for_infra_stop(candidate, exc, facts_client)
                    verdict, reason = judged
                    judgement_evidence = list(getattr(judged, "evidence", []) or [])
                    must_beat = list(getattr(judged, "must_beat", []) or [])
                    combined_priors = list(getattr(exc, "verified", None) or [])
                    problem_alternatives = combined_priors
                except RelevanceLLMError as exc:
                    if "refusing to score with a small local model" in str(exc):
                        raise
                    # A transient Nightshift failure (e.g. HTTP 502 after retries) must not
                    # abort the whole run: this repo gets NEEDS_REVIEW and scoring continues.
                    verdict = "UNJUDGED"
                    reason = f"UNJUDGED (LLM_UNAVAILABLE): {exc}. Relevance/judgment step failed after retries; no judgment was made. Rerun this repo (finished calls are cached)."
                    trace.event(
                        agent_name="orchestrator",
                        action="DISCOVERY_FILTER",
                        input_summary={"repo_id": repo_id},
                        output_summary=_discovery_output(verdict, reason, evidence_mode),
                        status="ok",
                    )
                    candidate.pop("_manifest_texts", None)
                    ineligible_repos.append(
                        {"repo_id": repo_id, "verdict": verdict, "reason": reason, "eligibility": False}
                    )
                    continue
                if verdict == "CANDIDATE" and getattr(search_results, "insufficient_coverage", False) and len(combined_priors) == 0:
                    verdict = "NEEDS_REVIEW"
                    reason = "INSUFFICIENT_PRIOR_ART_COVERAGE: absence of alternatives is not evidence of no competition"
                prior_for_repo = combined_priors
        else:
            judged = judge_discovery(
                candidate, prior_for_repo, demand_for_repo, client=relevance_client
            )
            verdict, reason = judged
            judgement_evidence = list(getattr(judged, "evidence", []) or [])
            must_beat = list(getattr(judged, "must_beat", []) or [])

        if verdict == "CANDIDATE" and len(prior_for_repo) == 0:
            verdict = "NEEDS_PRIOR_ART"
            reason = "Zero alternatives checked in prior-art search."

        if verdict not in {"ALREADY_EXISTS", "NEEDS_REVIEW"}:
            judgement_evidence = []

        candidate.pop("_manifest_texts", None)

        # Emit exactly one final DISCOVERY_FILTER event per repo
        trace.event(
            agent_name="orchestrator",
            action="DISCOVERY_FILTER",
            input_summary={"repo_id": repo_id},
            output_summary=_discovery_output(
                verdict, reason, evidence_mode, evidence=judgement_evidence, must_beat=must_beat
            ),
            status="ok",
            summary_limit=EVIDENCE_SUMMARY_LIMIT,
        )

        if verdict == "CANDIDATE":
            eligible_candidates.append(
                {
                    "candidate": candidate,
                    "prior_rows": prior_for_repo,
                    "demand_rows": demand_for_repo,
                    "verdict": verdict,
                    "reason": reason,
                    "must_beat": must_beat,
                }
            )
        else:
            row = {
                "repo_id": repo_id,
                "verdict": verdict,
                "reason": reason,
                "eligibility": False,
            }
            if verdict in {"ALREADY_EXISTS", "NEEDS_REVIEW"}:
                row["decision_evidence"] = judgement_evidence
            if must_beat:
                row["must_beat"] = must_beat
            ineligible_repos.append(row)

    evaluated_scorecards: list[Scorecard] = []
    candidates_output: list[dict[str, Any]] = []

    # 2. Process each CANDIDATE
    for item in eligible_candidates:
        candidate = item["candidate"]
        prior_rows = item["prior_rows"]
        demand_rows = item["demand_rows"]
        repo_id = candidate["repo_id"]

        # Seed evidence
        evidence_items = evidence_from_rows(candidate, prior_rows, demand_rows)
        evidence_index = {str(e.get("id") or ""): e for e in evidence_items}
        evidence_set = set(evidence_index.keys())

        # Extract candidate capabilities with relevance.py
        cand_caps, meta_caps = extract_capabilities(candidate, client=relevance_client)
        fallback_caps = bool(meta_caps.get("fallback"))
        flags: list[str] = []
        if fallback_caps:
            flags.append("LLM_FALLBACK")
        if relevance_client and repo_id in relevance_client.repo_mismatches:
            if "MODEL_MISMATCH" not in flags:
                flags.append("MODEL_MISMATCH")

        # Baseline criteria
        criteria_map, initial_accepted = deterministic_baseline_analyst(
            candidate=candidate,
            prior_rows=prior_rows,
            demand_rows=demand_rows,
            evidence_index=evidence_index,
            candidate_capabilities=cand_caps,
            relevance_client=relevance_client,
            fallback_caps=fallback_caps,
        )

        # Optional LLM attempt
        if use_llm:
            llm_success = False
            if llm_bound is not None and llm_prompt is not None:
                retry_count = 0
                try:
                    messages = [
                        {"role": "system", "content": llm_prompt.text},
                        {
                            "role": "user",
                            "content": f"ROLE: venture_analyst\nREPO: {repo_id}\nEVIDENCE:\n"
                            + json.dumps(evidence_items, ensure_ascii=False),
                        },
                    ]
                    raw_reply = llm_bound.complete(messages)
                    from app.llm.parsing import parse_model_json

                    try:
                        parsed = parse_model_json(raw_reply)
                        if not (isinstance(parsed, dict) and "criteria" in parsed):
                            raise ValueError("Invalid model JSON: missing 'criteria'")
                    except Exception:
                        retry_count = 1
                        messages.append({"role": "assistant", "content": raw_reply})
                        messages.append({"role": "user", "content": "Return only the JSON object matching the schema."})
                        raw_reply = llm_bound.complete(messages)
                        parsed = parse_model_json(raw_reply)
                        if not (isinstance(parsed, dict) and "criteria" in parsed):
                            raise ValueError("Invalid model JSON: missing 'criteria' on retry")

                    llm_criteria = parsed["criteria"]
                    for cname in CRITERIA_NAMES:
                        if cname in llm_criteria and isinstance(llm_criteria[cname], dict):
                            c_data = llm_criteria[cname]
                            criteria_map[cname] = Criterion(
                                score=int(c_data.get("score", 0)),
                                evidence_ids=list(c_data.get("evidence_ids") or []),
                                rationale=str(c_data.get("rationale") or ""),
                            )
                    llm_success = True
                    trace.event(
                        agent_name="venture_analyst",
                        action="LLM_SCORE",
                        input_summary={"repo_id": repo_id},
                        output_summary={"parsed": True},
                        retry_count=retry_count,
                        llm={
                            "requested_model": getattr(llm_bound, "requested_model", ""),
                            "served_model": getattr(llm_bound, "served_model", None),
                            "prompt_version": llm_prompt.sha256,
                        },
                        status="ok",
                    )
                except Exception as exc:
                    llm_success = False
                    trace.event(
                        agent_name="venture_analyst",
                        action="LLM_FALLBACK",
                        input_summary={"repo_id": repo_id, "error": str(exc)},
                        output_summary="Falling back to deterministic baseline analyst.",
                        retry_count=retry_count,
                        status="LLM_FALLBACK",
                    )
            else:
                trace.event(
                    agent_name="venture_analyst",
                    action="LLM_FALLBACK",
                    input_summary={"repo_id": repo_id, "error": "LLM client not configured"},
                    output_summary="Falling back to deterministic baseline analyst.",
                    status="LLM_FALLBACK",
                )

            if not llm_success and "LLM_FALLBACK" not in flags:
                flags.append("LLM_FALLBACK")

        # Initial scorecard
        scorecard = Scorecard(
            repo_id=repo_id,
            verdict="CANDIDATE",
            criteria=criteria_map,
            evidence_set=evidence_set,
            metadata={"alternative_repos": [r.get("alternative_id") for r in prior_rows if r.get("alternative_id")]},
        )

        trace.event(
            agent_name="venture_analyst",
            action="INITIAL_SCORECARD",
            input_summary={"repo_id": repo_id},
            output_summary={"total": scorecard.total, "feasibility": scorecard.feasibility_score},
            status="ok",
        )

        # 4. Review and reinvestigate
        def make_analyst(c_map, cand_item, p_rows, d_rows, caps, rel_client, fb_caps):
            curr_accepted = list(initial_accepted)

            def analyst(criterion: str, repo_id: str, evidence: list[dict[str, Any]], scorecard: Scorecard, **kwargs: Any) -> Criterion:
                nonlocal curr_accepted
                if criterion == "demand_evidence":
                    crit, acc = evaluate_demand_evidence(
                        candidate=cand_item,
                        demand_rows=d_rows,
                        evidence_items=evidence,
                        candidate_capabilities=caps,
                        relevance_client=rel_client,
                        fallback_caps=fb_caps,
                    )
                    curr_accepted = acc
                    return crit
                if criterion == "differentiation":
                    ev_idx = {str(e.get("id")): e for e in evidence if e.get("id")}
                    crit = compute_differentiation(
                        candidate=cand_item,
                        prior_rows=p_rows,
                        demand_rows=d_rows,
                        evidence_index=ev_idx,
                        candidate_capabilities=caps,
                        relevance_client=rel_client,
                        fallback_caps=fb_caps,
                        accepted_demand_signals=curr_accepted,
                    )
                    return crit
                return c_map.get(criterion, Criterion(score=0, evidence_ids=[], rationale="No evidence"))

            return analyst, lambda: curr_accepted

        analyst_callable, get_curr_accepted = make_analyst(
            criteria_map, candidate, prior_rows, demand_rows, cand_caps, relevance_client, fallback_caps
        )
        reinvestigation = reinvestigate(
            scorecard=scorecard,
            evidence=evidence_items,
            toolbox=toolbox,
            analyst=analyst_callable,
            max_rounds=2,
        )

        scorecard = reinvestigation.scorecard
        final_evidence_items = reinvestigation.evidence
        final_evidence_set = {str(e.get("id")) for e in final_evidence_items if e.get("id")}

        # Ensure differentiation reflects any newly accepted demand signals and final evidence
        ev_idx_final = {str(e.get("id")): e for e in final_evidence_items if e.get("id")}
        final_diff = compute_differentiation(
            candidate=candidate,
            prior_rows=prior_rows,
            demand_rows=demand_rows,
            evidence_index=ev_idx_final,
            candidate_capabilities=cand_caps,
            relevance_client=relevance_client,
            fallback_caps=fallback_caps,
            accepted_demand_signals=get_curr_accepted(),
        )
        scorecard.criteria["differentiation"] = final_diff
        scorecard.clamp(final_evidence_set)

        for f in reinvestigation.flags:
            if f not in flags:
                flags.append(f)
        if relevance_client and repo_id in relevance_client.repo_mismatches:
            if "MODEL_MISMATCH" not in flags:
                flags.append("MODEL_MISMATCH")
        if any(c.status == "UNSUPPORTED" for c in scorecard.criteria.values()):
            if "UNSUPPORTED" not in flags:
                flags.append("UNSUPPORTED")

        evaluated_scorecards.append(scorecard)

        # Build candidate output structure
        criteria_dict = {}
        for cname in CRITERIA_NAMES:
            crit = scorecard.criteria.get(cname)
            if crit is not None:
                criteria_dict[cname] = {
                    "score": crit.score,
                    "original_score": crit.original_score,
                    "status": crit.status,
                    "rationale": crit.rationale,
                    "evidence_ids": crit.evidence_ids,
                }

        candidates_output.append(
            {
                "repo_id": repo_id,
                "verdict": "CANDIDATE",
                "eligibility": True,
                "total": scorecard.total,
                "feasibility": scorecard.feasibility_score,
                "flags": flags,
                "criteria": criteria_dict,
                "capabilities": cand_caps,
                "evidence": [
                    {
                        "id": e.get("id"),
                        "url": e.get("url"),
                        "text": e.get("text"),
                        "content_hash": e.get("content_hash"),
                    }
                    for e in final_evidence_items
                ],
                "must_beat": item.get("must_beat") or [],
            }
        )

    warnings: list[str] = []
    duplicated_vectors: list[list[int]] = []
    scorer_not_discriminating = False

    # 5. Check score vector discrimination on eligible candidates
    if len(evaluated_scorecards) > 2:
        vector_counts: dict[tuple[int, ...], int] = {}
        for sc in evaluated_scorecards:
            vec = tuple(sc.criteria[cname].score for cname in CRITERIA_NAMES)
            vector_counts[vec] = vector_counts.get(vec, 0) + 1
        max_count = max(vector_counts.values()) if vector_counts else 0
        if max_count / len(evaluated_scorecards) > 0.5:
            scorer_not_discriminating = True
            warnings.append("SCORER_NOT_DISCRIMINATING")
            duplicated_vectors = [list(vec) for vec, count in vector_counts.items() if count > 1]
            trace.event(
                agent_name="orchestrator",
                action="SCORER_NOT_DISCRIMINATING",
                input_summary={"candidate_count": len(evaluated_scorecards)},
                output_summary={"warning": "SCORER_NOT_DISCRIMINATING", "duplicated_vectors": duplicated_vectors},
                status="warning",
            )
            print("SCORER NOT DISCRIMINATING", file=sys.stderr)

    # 6. Select protagonist
    if scorer_not_discriminating:
        protagonist = None
        prot_reason = "Scorer not discriminating: more than 50% of candidates share identical score vectors."
        protagonist_data = {
            "repo_id": None,
            "total": 0.0,
            "feasibility": 0,
            "reason": prot_reason,
        }
    else:
        protagonist, prot_reason = select_protagonist(evaluated_scorecards)
        if protagonist is not None:
            protagonist_data = {
                "repo_id": protagonist.repo_id,
                "total": protagonist.total,
                "feasibility": protagonist.feasibility_score,
                "reason": prot_reason,
            }
        else:
            protagonist_data = {
                "repo_id": None,
                "total": 0.0,
                "feasibility": 0,
                "reason": prot_reason,
            }

    trace.event(
        agent_name="orchestrator",
        action="PROTAGONIST_SELECTION",
        input_summary={"candidate_count": len(evaluated_scorecards)},
        output_summary=protagonist_data,
        status="ok",
    )

    # 5b. Meterless H-MEM verdict memory: records each verdict, flags a change from the last
    # remembered one. It never changes a verdict and never stops the run (app/verdict_memory.py).
    from app.verdict_memory import remember as hmem_remember

    hmem_rows = [
        {"repo_id": r.get("repo_id"), "verdict": r.get("verdict"), "reason": r.get("reason")}
        for r in [*ineligible_repos, *candidates_output]
    ]
    try:
        hmem_summary = hmem_remember(hmem_rows, trace=trace, log_dir=active_log_dir, run_id=active_run_id)
    except Exception as exc:
        # remember() already catches its own failures. This is the last guard so a
        # bug there cannot skip the seal or change a verdict already stored on the row.
        hmem_summary = []
        try:
            trace.event(
                agent_name="hmem",
                action="HMEM_VERDICT_MEMORY",
                input_summary={"run_id": active_run_id},
                output_summary={"error": f"{type(exc).__name__}: {str(exc)[:200]}"},
                status="error",
            )
        except Exception:
            pass

    # 6. Seal trace
    run_hash = trace.seal()

    for row in ineligible_repos:
        row["evidence_source"] = evidence_mode
        row["rescue_potential"] = rescue_by_repo.get(str(row.get("repo_id") or ""))
    for row in candidates_output:
        row["evidence_source"] = evidence_mode
        row["rescue_potential"] = rescue_by_repo.get(str(row.get("repo_id") or ""))

    # 7. Render JSON and Markdown
    results_json = {
        "date": now_iso,
        "run_id": active_run_id,
        "run_hash": run_hash,
        "command": command_str or " ".join(sys.argv),
        "llm_used": bool(relevance_client and len(relevance_client.served_models) > 0),
        "model_calls": relevance_client.model_calls if relevance_client else {},
        "evidence_source": evidence_mode,
        "model_mismatch_count": trace.model_mismatch_count(),
        "warnings": warnings,
        "duplicated_vectors": duplicated_vectors,
        "duplicated_score_vectors": duplicated_vectors,
        "protagonist": protagonist_data,
        "candidates": candidates_output,
        "ineligible": ineligible_repos,
        "hmem_verdict_memory": hmem_summary,
    }

    active_output_json.parent.mkdir(parents=True, exist_ok=True)
    active_output_json.write_text(json.dumps(results_json, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Render Markdown
    md_content = _render_markdown(results_json)
    active_output_md.parent.mkdir(parents=True, exist_ok=True)
    active_output_md.write_text(md_content, encoding="utf-8")

    if scorer_not_discriminating:
        sys.exit(3)

    return results_json


def _render_markdown(data: dict[str, Any]) -> str:
    lines = []
    lines.append("# Venture Scorecard & Protagonist Selection")
    lines.append("")
    lines.append(f"- **Date**: {data['date']}")
    lines.append(f"- **Run Hash**: `{data['run_hash']}`")
    lines.append(f"- **Command**: `{data['command']}`")
    lines.append(f"- **Evidence source**: `{data.get('evidence_source') or ''}`")
    lines.append(f"- **LLM Evaluator**: {'Live Nightshift' if data.get('llm_used') else 'Deterministic Baseline Analyst'}")
    lines.append("")

    model_calls = data.get("model_calls") or {}
    lines.append("## Model Call Summary")
    lines.append("")
    lines.append(f"- **MODEL_MISMATCH**: {int(data.get('model_mismatch_count') or 0)}")
    lines.append("")
    if model_calls:
        lines.append("| Served Model | Calls |")
        lines.append("| :--- | :---: |")
        for m, count in sorted(model_calls.items()):
            lines.append(f"| `{m}` | {count} |")
    else:
        lines.append("No LLM calls recorded.")
    lines.append("")

    warnings = data.get("warnings") or []
    if warnings:
        lines.append("## Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- **WARNING**: `{w}`")
        if data.get("duplicated_vectors"):
            lines.append(f"- *Duplicated score vectors*: `{data.get('duplicated_vectors')}`")
        lines.append("")

    lines.append("## Protagonist Selection")
    lines.append("")
    eligible_count = len(data.get("candidates") or [])
    lines.append(f"- **Eligible Candidates**: {eligible_count}")
    if eligible_count == 1:
        lines.append("- **Note**: selection is trivial: only one eligible candidate.")
    lines.append("")
    prot = data.get("protagonist") or {}
    if prot.get("repo_id"):
        lines.append(f"**Selected Protagonist**: [`{prot['repo_id']}`](https://github.com/{prot['repo_id']})")
        lines.append(f"- **Weighted Total**: {prot.get('total')}/100")
        lines.append(f"- **Feasibility Score**: {prot.get('feasibility')}/3")
        lines.append(f"- **Selection Reason**: {prot.get('reason')}")
    else:
        lines.append(f"**No Protagonist Selected**: {prot.get('reason')}")
    lines.append("")

    lines.append("## Eligible Candidates Ranking")
    lines.append("")
    lines.append("| Rank | Repository | Total | Feasibility | Problem | Demand | Diff | Feas | Prod | Moat | Flags |")
    lines.append("| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |")

    sorted_candidates = sorted(data.get("candidates") or [], key=lambda c: (-c["total"], c["repo_id"]))
    for idx, c in enumerate(sorted_candidates, start=1):
        crit = c.get("criteria") or {}
        p = crit.get("problem_severity", {}).get("score", 0)
        d = crit.get("demand_evidence", {}).get("score", 0)
        df = crit.get("differentiation", {}).get("score", 0)
        f = crit.get("feasibility", {}).get("score", 0)
        pr = crit.get("productization_path", {}).get("score", 0)
        m = crit.get("moat", {}).get("score", 0)
        flags_str = ", ".join(c.get("flags") or []) or "None"
        repo = f"[`{c['repo_id']}`](https://github.com/{c['repo_id']})"
        lines.append(f"| {idx} | {repo} | **{c['total']}** | {c['feasibility']} | {p} | {d} | {df} | {f} | {pr} | {m} | `{flags_str}` |")
    lines.append("")

    lines.append("## Candidate Criteria Breakdown")
    lines.append("")
    for c in sorted_candidates:
        lines.append(f"### {c['repo_id']} (Total: {c['total']}/100)")
        lines.append("")
        for cname in CRITERIA_NAMES:
            cinfo = (c.get("criteria") or {}).get(cname) or {}
            score = cinfo.get("score", 0)
            status = cinfo.get("status", "SUPPORTED")
            rationale = cinfo.get("rationale", "")
            eids = ", ".join(cinfo.get("evidence_ids") or []) or "none"
            lines.append(f"- **{cname}**: {score}/3 ({status})")
            lines.append(f"  - *Rationale*: {rationale}")
            lines.append(f"  - *Evidence IDs*: `{eids}`")
        lines.append("")

    lines.append("## Ineligible Repositories")
    lines.append("")
    lines.append("| Repository | Verdict | Reason |")
    lines.append("| :--- | :---: | :--- |")
    for inel in data.get("ineligible") or []:
        lines.append(f"| [`{inel['repo_id']}`](https://github.com/{inel['repo_id']}) | `{inel['verdict']}` | {inel['reason']} |")
    lines.append("")
    from scripts.evidence_report import format_decision_lines

    lines.append("## Rescue potential")
    lines.append("")
    lines.append("This score does not change the verdict. A refused repository stays refused.")
    lines.append("")
    lines.append("| Repository | Verdict | Score | Stars | Stale days | Stars vs staleness | CI | External breakage | Dependabot | Renovate |")
    lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :--- | :---: | :---: | :---: |")
    ranked = list(data.get("candidates") or []) + list(data.get("ineligible") or [])
    ranked.sort(key=lambda row: -float(((row.get("rescue_potential") or {}).get("score") or 0)))
    for row in ranked:
        rescue = row.get("rescue_potential") or {}
        comp = rescue.get("components") or {}
        lines.append(
            "| `{repo}` | `{verdict}` | {score} | {stars} | {stale} | {svs} | {ci} | {brk} | {dep} | {ren} |".format(
                repo=row.get("repo_id"),
                verdict=row.get("verdict"),
                score=rescue.get("score", ""),
                stars=comp.get("stars", ""),
                stale=comp.get("staleness_days", ""),
                svs=comp.get("stars_vs_staleness", ""),
                ci=comp.get("ci_state", ""),
                brk=comp.get("external_breakage", ""),
                dep=comp.get("dependabot_open", ""),
                ren=comp.get("renovate_open", ""),
            )
        )
    lines.append("")
    lines.append("## Why a repository was refused or sent to review")
    lines.append("")
    shown = False
    for inel in data.get("ineligible") or []:
        block = format_decision_lines(
            str(inel.get("repo_id") or ""),
            str(inel.get("verdict") or ""),
            str(inel.get("reason") or ""),
            inel.get("decision_evidence"),
        )
        if block:
            shown = True
            lines.extend(block)
    if not shown:
        lines.append("No ALREADY_EXISTS or NEEDS_REVIEW row was recorded.")
        lines.append("")
    lines.append("## Alternatives that did not block")
    lines.append("")
    beat = False
    for row in list(data.get("candidates") or []) + list(data.get("ineligible") or []):
        notes = row.get("must_beat") or []
        if not notes:
            continue
        beat = True
        lines.append(f"### `{row.get('repo_id')}`")
        lines.append("")
        lines.append("| Alternative | Overlap | Verified quote | Why it did not block |")
        lines.append("| :--- | :--- | :--- | :--- |")
        for note in notes:
            cells = [str(note.get(k) or "").replace("|", "/").replace("\n", " ") for k in ("alternative", "overlap", "quote", "reason")]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    if not beat:
        lines.append("None.")
        lines.append("")

    return "\n".join(lines)


def _env_number(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else default
    except ValueError:
        return default
    return value if value > 0 else default


def _evidence_mode(explicit: str | None) -> str:
    if explicit is None or not str(explicit).strip():
        return resolve_evidence_source()
    mode = str(explicit).strip().lower()
    if mode not in {"zetaris", "csv"}:
        raise ValueError("EVIDENCE_SOURCE must be zetaris or csv.")
    return mode


def _discovery_output(
    verdict: str,
    reason: str,
    evidence_mode: str,
    evidence: list[dict[str, Any]] | None = None,
    must_beat: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"verdict": verdict, "reason": reason, "evidence_source": evidence_mode}
    if evidence is not None:
        payload["evidence"] = evidence
    if must_beat is not None:
        payload["must_beat"] = must_beat
    return payload


def _is_infra_failure(err: str) -> bool:
    """True when an LLM failure is infrastructure (5xx, 429, timeout, invalid JSON), not evidence."""
    low = str(err or "").lower()
    return any(k in low for k in ("http 5", "http 429", "timed out", "timeout", "nightshift request", "invalid json", "exceeded total timeout"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Score candidate repos and select the demo protagonist.")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "real", help="Directory with discovery CSVs.")
    parser.add_argument("--snapshot", type=Path, default=None, help="Directory with discovery CSVs (overrides --data-dir).")
    parser.add_argument("--output-json", type=Path, default=ROOT / "docs" / "revival" / "venture_scores.json")
    parser.add_argument("--output-md", type=Path, default=ROOT / "docs" / "revival" / "venture_scores.md")
    parser.add_argument("--max-calls", type=int, default=40, help="Toolbox max tool calls.")
    parser.add_argument("--max-seconds", type=float, default=300.0, help="Toolbox max runtime in seconds.")
    parser.add_argument("--llm", action="store_true", help="Use live Nightshift LLM evaluator.")
    parser.add_argument("--run-id", type=str, default=None, help="Custom trace run ID.")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "logs", help="Directory for trace logs.")
    args = parser.parse_args()

    from app.agents.repo_facts import RepoFactsClient

    facts_dir = (args.snapshot or args.data_dir) / ".cache" / "repo_facts"
    try:
        results = score_all(
            data_dir=args.data_dir,
            snapshot=args.snapshot,
            output_json=args.output_json,
            output_md=args.output_md,
            max_calls=args.max_calls,
            max_seconds=args.max_seconds,
            use_llm=args.llm,
            run_id=args.run_id,
            log_dir=args.log_dir,
            command_str=" ".join(sys.argv),
            facts_client=RepoFactsClient(cache_dir=facts_dir),
        )
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    except ZetarisPreflightError as exc:
        sys.stderr.write(f"Scoring not started: {exc}\n")
        return 2
    except RelevanceLLMError as exc:
        sys.stderr.write(f"Scoring aborted: {exc}\n")
        return 1

    prot = results.get("protagonist") or {}
    print(f"Run hash: {results.get('run_hash')}")
    if prot.get("repo_id"):
        print(f"Selected Protagonist: {prot['repo_id']} (Total: {prot.get('total')}, Feasibility: {prot.get('feasibility')})")
    else:
        print(f"No protagonist selected: {prot.get('reason')}")
    return 0


if __name__ == "__main__":
    from app.console import prepare_cli

    prepare_cli()
    raise SystemExit(main())
