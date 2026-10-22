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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.equivalence import evidence_from_rows  # noqa: E402
from app.agents.market_filter import (  # noqa: E402
    alternative_qualifies,
    decide_discovery,
    demand_qualifies,
    is_boilerplate,
    observed_days,
    split_terms,
)
from app.agents.reinvestigate import reinvestigate  # noqa: E402
from app.agents.subagents import SubAgentManager, is_candidate_repo_url  # noqa: E402
from app.agents.venture import (  # noqa: E402
    CRITERIA_NAMES,
    Criterion,
    Scorecard,
    review_scorecard,
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
    ZetarisSqlTool,
)
from app.trace_log import HackathonTrace  # noqa: E402
from config.settings import Settings  # noqa: E402


def _read_csv(path: Path, expected_columns: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.exists():
        return []
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


def compute_differentiation(
    candidate: dict[str, Any],
    prior_rows: list[dict[str, Any]],
    demand_rows: list[dict[str, Any]],
    evidence_index: dict[str, dict[str, Any]],
) -> Criterion:
    """Compute differentiation score 0-3 based on capability gap vs maintained free OSS alternatives.

    Score 0 if no missing capabilities covered by candidate.
    Score 1 if one missing capability is shown by an exact quote from the candidate evidence.
    Score 2 if two or more missing capabilities are shown by exact candidate quotes.
    Score 3 only with an additional external quote showing users asking for that capability.
    Rationale must name each missing capability, the alternatives checked, and quote the candidate text.
    """
    repo_id = str(candidate.get("repo_id") or "")
    cand_ev_id = f"candidate:{repo_id}"
    cand_ev = evidence_index.get(cand_ev_id)
    cand_text = str((cand_ev or {}).get("text") or "")
    if not cand_text:
        cand_text = "\n".join(
            p
            for p in (
                str(candidate.get("description") or ""),
                str(candidate.get("readme_excerpt") or ""),
                str(candidate.get("problem_excerpt") or ""),
                str(candidate.get("user_excerpt") or ""),
                str(candidate.get("scope_excerpt") or ""),
            )
            if p
        )

    required_terms = split_terms(candidate.get("core_terms"))
    if not required_terms:
        for r in prior_rows:
            rt = split_terms(r.get("required_terms"))
            if rt:
                required_terms = rt
                break

    # Identify maintained free OSS alternatives: licence in FREE_OSS_LICENCES and days <= 90
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

    # For each required term, check:
    # 1. Does any maintained free alternative cover it?
    # 2. Does candidate cover it with an exact quote in cand_text?
    missing_capabilities: list[tuple[str, str]] = []  # (term, candidate_quote)
    covered_by_free: dict[str, str] = {}

    for term in required_terms:
        alt_cover = None
        for mf in maintained_free_alts:
            alt_excerpt = str(mf.get("excerpt") or "")
            if term.lower() in alt_excerpt.lower():
                alt_cover = str(mf.get("alternative_id") or "")
                break
        if alt_cover is not None:
            covered_by_free[term] = alt_cover
            continue

        if cand_text and term.lower() in cand_text.lower():
            quote = extract_exact_quote(cand_text, term)
            if quote and quote in cand_text:
                missing_capabilities.append((term, quote))

    # Build description of alternatives checked
    alts_checked_desc: list[str] = []
    for mf in maintained_free_alts:
        alt_id = str(mf.get("alternative_id") or "")
        lic = str(mf.get("licence_spdx") or "")
        days = observed_days(mf)
        covered = [t for t, a in covered_by_free.items() if a == alt_id]
        if covered:
            alts_checked_desc.append(f"{alt_id} (maintained free {lic}, {days}d; covers {', '.join(covered)})")
        else:
            alts_checked_desc.append(f"{alt_id} (maintained free {lic}, {days}d; lacks all missing capabilities)")
    for oth in other_alts:
        alt_id = str(oth.get("alternative_id") or "")
        lic = str(oth.get("licence_spdx") or "no licence")
        days = observed_days(oth)
        reason = []
        if lic not in FREE_OSS_LICENCES:
            reason.append(f"licence={lic}")
        if days is None or days > ABANDONED_AFTER_DAYS:
            reason.append(f"unmaintained {days}d")
        alts_checked_desc.append(f"{alt_id} ({', '.join(reason)})")

    # Check for external quote showing users asking for a missing capability (for score 3)
    external_user_quote: tuple[str, str, str, str] | None = None
    for term, _ in missing_capabilities:
        for d in demand_rows:
            d_excerpt = str(d.get("excerpt") or "").strip()
            d_url = str(d.get("url") or "")
            d_sig = str(d.get("signal_id") or "")
            if not _is_user_request_url(d_url):
                continue
            if term.lower() in d_excerpt.lower():
                eq = extract_exact_quote(d_excerpt, term, max_chars=180)
                if eq and eq in d_excerpt:
                    external_user_quote = (term, eq, d_url, f"demand:{d_sig}")
                    break
        if external_user_quote is not None:
            break
        for eid, ev in evidence_index.items():
            ev_url = str(ev.get("url") or "")
            if is_candidate_repo_url(ev_url, repo_id) or repo_id.lower() in ev_url.lower():
                continue
            # Only a user request counts (issue/discussion/forum), not a PR, code or repo page.
            if not _is_user_request_url(ev_url):
                continue
            ev_text = str(ev.get("text") or "").strip()
            if term.lower() in ev_text.lower():
                eq = extract_exact_quote(ev_text, term, max_chars=180)
                if eq and eq in ev_text:
                    external_user_quote = (term, eq, ev_url, eid)
                    break
        if external_user_quote is not None:
            break

    # Determine score
    num_missing = len(missing_capabilities)
    if num_missing == 0:
        score = 0
    elif num_missing == 1:
        score = 1
    else:
        if external_user_quote is not None:
            score = 3
        else:
            score = 2

    ev_ids = [cand_ev_id]
    for r in prior_rows:
        pid = f"prior:{r.get('alternative_id')}"
        if pid in evidence_index and pid not in ev_ids:
            ev_ids.append(pid)
    if score == 3 and external_user_quote is not None:
        ext_eid = external_user_quote[3]
        if ext_eid in evidence_index and ext_eid not in ev_ids:
            ev_ids.append(ext_eid)

    missing_names = [f"'{t}'" for t, _ in missing_capabilities]
    missing_str = ", ".join(missing_names) if missing_names else "none"
    quotes_str = "; ".join(f"for '{t}': \"{q}\"" for t, q in missing_capabilities) if missing_capabilities else "none"
    alts_str = "; ".join(alts_checked_desc) if alts_checked_desc else "none"

    if score == 0:
        rationale = f"Score 0: No missing capabilities. Required capabilities ({', '.join(required_terms)}) are covered by maintained free OSS alternatives. Alternatives checked: {alts_str}."
    elif score == 1:
        rationale = f"Score 1: Missing capability {missing_str} not covered by any maintained free OSS alternative. Alternatives checked: {alts_str}. Candidate quote: {quotes_str}."
    elif score == 2:
        rationale = f"Score 2: Missing capabilities ({missing_str}) not covered by any maintained free OSS alternative. Alternatives checked: {alts_str}. Candidate quotes: {quotes_str}."
    else:
        ext_term, ext_q, ext_u, _ = external_user_quote
        rationale = f"Score 3: Missing capabilities ({missing_str}) not covered by any maintained free OSS alternative with external user demand pull. Alternatives checked: {alts_str}. Candidate quotes: {quotes_str}. External user demand quote for '{ext_term}': \"{ext_q}\" ({ext_u})."

    return Criterion(
        score=score,
        evidence_ids=ev_ids,
        rationale=rationale,
    )


def evaluate_demand_evidence(
    candidate: dict[str, Any],
    demand_rows: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
) -> Criterion:
    """Evaluate demand_evidence criterion under strict anti-inflation rules.

    A tool evidence item counts only if:
    - its stored text is non-empty
    - AND it contains at least one of the candidate's core_terms (case-insensitive)
    - AND its URL is not the candidate repo.

    Scoring:
    - Score 1 for one qualifying external item
    - Score 2 for at least two qualifying items from different sources
    - Score 3 only for at least three from different sources including one non-GitHub source
    - Rationale must quote the matching snippet (<=200 chars) and its URL.
    - Fall back to a stored qualifying demand signal (score 1, quote it).
    - Otherwise 0.
    """
    repo_id = str(candidate.get("repo_id") or "")
    core_terms = split_terms(candidate.get("core_terms"))

    # 1. Check tool evidence items
    qualifying_tool_items: list[tuple[dict[str, Any], str]] = []
    for item in evidence_items:
        iid = str(item.get("id") or "")
        if not iid.startswith("tool:"):
            continue
        text = str(item.get("text") or "").strip()
        if not text or text == "None":
            continue
        url = str(item.get("url") or "").strip()
        if not url or is_candidate_repo_url(url, repo_id) or repo_id.lower() in url.lower():
            continue
        matched = [t for t in core_terms if t.lower() in text.lower()]
        if not matched:
            continue
        qualifying_tool_items.append((item, matched[0]))

    if qualifying_tool_items:
        unique_sources: dict[str, tuple[dict[str, Any], str]] = {}
        for item, term in qualifying_tool_items:
            src = get_evidence_source(item)
            if src not in unique_sources:
                unique_sources[src] = (item, term)

        num_sources = len(unique_sources)
        has_non_gh = any(is_non_github_source(s) for s in unique_sources.keys())

        if num_sources >= 3 and has_non_gh:
            score = 3
        elif num_sources >= 2:
            score = 2
        else:
            score = 1

        selected_items = list(unique_sources.values())[:score]
        evidence_ids = [it.get("id") for it, _ in selected_items]

        quotes = []
        for it, t in selected_items:
            snip = extract_matching_snippet(str(it.get("text") or ""), t, max_len=180)
            quotes.append(f'"{snip}" ({it.get("url")})')
        rationale = f"Verified external demand pull ({score}/3) from {num_sources} source(s): " + "; ".join(quotes)

        return Criterion(
            score=score,
            evidence_ids=evidence_ids,
            rationale=rationale,
        )

    # 2. Fall back to stored qualifying demand signal
    qualifying_demand = [r for r in demand_rows if demand_qualifies(r, core_terms, repo_id)]
    if qualifying_demand:
        sig = qualifying_demand[0]
        sig_id = str(sig.get("signal_id") or "")
        sig_url = str(sig.get("url") or "")
        sig_excerpt = str(sig.get("excerpt") or "").strip()
        matched = [t for t in core_terms if t.lower() in sig_excerpt.lower()]
        term = matched[0] if matched else (core_terms[0] if core_terms else "")
        snip = extract_matching_snippet(sig_excerpt, term, max_len=180)
        return Criterion(
            score=1,
            evidence_ids=[f"demand:{sig_id}"],
            rationale=f'Stored qualifying external demand signal from {sig_url} ({sig_id}): "{snip}".',
        )

    # 3. Otherwise 0
    return Criterion(
        score=0,
        evidence_ids=[],
        rationale="No qualifying external demand items or stored demand signals found outside candidate repo containing core terms.",
    )


def _is_user_request_url(url: str) -> bool:
    """True for sources where users ask for something: GitHub issues/discussions or a non-GitHub page."""
    u = (url or "").lower()
    if "github.com" not in u:
        return bool(u.startswith("http"))
    return "/issues/" in u or "/discussions/" in u

def deterministic_baseline_analyst(
    candidate: dict[str, Any],
    prior_rows: list[dict[str, Any]],
    demand_rows: list[dict[str, Any]],
    evidence_index: dict[str, dict[str, Any]],
) -> dict[str, Criterion]:
    """Deterministic baseline analyst scoring the six venture criteria using verified evidence."""
    repo_id = str(candidate.get("repo_id") or "")
    cand_ev_id = f"candidate:{repo_id}"
    terms = split_terms(candidate.get("core_terms"))

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

    # 2. Demand Evidence (YC "make something people want", strictly OUTSIDE repo)
    alts = [r.get("alternative_id") for r in prior_rows if r.get("alternative_id")]
    qualifying_demand = [r for r in demand_rows if demand_qualifies(r, terms, repo_id)]
    if qualifying_demand:
        # Initially empty evidence_ids triggers review_scorecard rework and sub-agent reinvestigation
        demand_crit = Criterion(
            score=2,
            evidence_ids=[],
            rationale=f"Candidate has potential external pull on alternatives ({', '.join(alts[:2])}). Requires subagent verification.",
        )
    else:
        demand_crit = Criterion(
            score=0,
            evidence_ids=[],
            rationale="No qualifying external demand signal found outside the candidate repository.",
        )

    # 3. Differentiation (functional gap vs free maintained OSS alternatives)
    diff_crit = compute_differentiation(candidate, prior_rows, demand_rows, evidence_index)

    # 4. Feasibility (code state, dependencies, architecture)
    paths = [p for p in str(candidate.get("code_paths") or "").split("|") if p and not is_boilerplate(p)]
    if len(paths) >= 2 and cand_ev_id in evidence_index:
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
    user_excerpt = str(candidate.get("user_excerpt") or "").strip()
    if user_excerpt and cand_ev_id in evidence_index:
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

    return {
        "problem_severity": problem_crit,
        "demand_evidence": demand_crit,
        "differentiation": diff_crit,
        "feasibility": feas_crit,
        "productization_path": prod_crit,
        "moat": moat_crit,
    }


def build_default_toolbox(
    max_calls: int = 40,
    max_seconds: float = 300.0,
    trace: HackathonTrace | None = None,
    custom_tools: dict[str, Any] | None = None,
) -> ToolBox:
    """Instantiate real tools and global budget."""
    if custom_tools is not None:
        return ToolBox(tools=custom_tools, budget=Budget(max_calls=max_calls, max_seconds=max_seconds), trace=trace)

    tools = {
        "github": GitHubTool(),
        "npm": NpmTool(),
        "pypi": PyPiTool(),
        "web_fetch": WebFetchTool(),
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


def score_all(
    data_dir: Path,
    output_json: Path,
    output_md: Path,
    max_calls: int = 40,
    max_seconds: float = 300.0,
    use_llm: bool = False,
    run_id: str | None = None,
    log_dir: Path | None = None,
    custom_toolbox: ToolBox | None = None,
    command_str: str = "",
) -> dict[str, Any]:
    """Main execution pipeline for candidate scoring and protagonist selection."""
    now_iso = datetime.now(timezone.utc).isoformat()
    active_run_id = run_id or f"venture-{uuid.uuid4().hex[:12]}"
    active_log_dir = log_dir or (ROOT / "logs")
    trace = HackathonTrace(run_id=active_run_id, log_dir=active_log_dir)

    toolbox = custom_toolbox or build_default_toolbox(
        max_calls=max_calls, max_seconds=max_seconds, trace=trace
    )

    # 1. Read discovery CSVs
    candidates_raw = _read_csv(data_dir / "discovery_candidates.csv", DISCOVERY_COLUMNS)
    prior_raw = _read_csv(data_dir / "prior_art.csv", PRIOR_ART_COLUMNS)
    demand_raw = _read_csv(data_dir / "demand_signals.csv", DEMAND_COLUMNS)

    eligible_candidates: list[dict[str, Any]] = []
    ineligible_repos: list[dict[str, Any]] = []

    for candidate in candidates_raw:
        repo_id = candidate["repo_id"]
        prior_for_repo = [r for r in prior_raw if r["repo_id"] == repo_id]
        demand_for_repo = [r for r in demand_raw if r["repo_id"] == repo_id]

        verdict, reason = decide_discovery(candidate, prior_for_repo, demand_for_repo)
        trace.event(
            agent_name="orchestrator",
            action="DISCOVERY_FILTER",
            input_summary={"repo_id": repo_id},
            output_summary={"verdict": verdict, "reason": reason},
            status="ok",
        )

        if verdict == "CANDIDATE":
            eligible_candidates.append(
                {
                    "candidate": candidate,
                    "prior_rows": prior_for_repo,
                    "demand_rows": demand_for_repo,
                    "verdict": verdict,
                    "reason": reason,
                }
            )
        else:
            ineligible_repos.append(
                {
                    "repo_id": repo_id,
                    "verdict": verdict,
                    "reason": reason,
                    "eligibility": False,
                }
            )

    evaluated_scorecards: list[Scorecard] = []
    candidates_output: list[dict[str, Any]] = []

    # 2. Setup LLM if requested
    llm_bound = None
    llm_prompt = None
    if use_llm:
        try:
            from app.llm.provider import build_llm

            llm_bound = build_llm(Settings(), sample=False)
            llm_prompt = load_prompt("venture_analyst")
        except Exception:
            llm_bound = None

    # 3. Process each CANDIDATE
    for item in eligible_candidates:
        candidate = item["candidate"]
        prior_rows = item["prior_rows"]
        demand_rows = item["demand_rows"]
        repo_id = candidate["repo_id"]

        # Seed evidence
        evidence_items = evidence_from_rows(candidate, prior_rows, demand_rows)
        evidence_index = {str(e.get("id") or ""): e for e in evidence_items}
        evidence_set = set(evidence_index.keys())

        # Baseline criteria
        criteria_map = deterministic_baseline_analyst(
            candidate, prior_rows, demand_rows, evidence_index
        )
        flags: list[str] = []

        # Optional LLM attempt
        if use_llm:
            llm_success = False
            if llm_bound is not None and llm_prompt is not None:
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

                    parsed = parse_model_json(raw_reply)
                    if isinstance(parsed, dict) and "criteria" in parsed:
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

            if not llm_success:
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
        def make_analyst(c_map, cand_item, p_rows, d_rows):
            def analyst(criterion: str, repo_id: str, evidence: list[dict[str, Any]], scorecard: Scorecard, **kwargs: Any) -> Criterion:
                if criterion == "demand_evidence":
                    return evaluate_demand_evidence(cand_item, d_rows, evidence)
                if criterion == "differentiation":
                    ev_idx = {str(e.get("id")): e for e in evidence if e.get("id")}
                    return compute_differentiation(cand_item, p_rows, d_rows, ev_idx)
                return c_map.get(criterion, Criterion(score=0, evidence_ids=[], rationale="No evidence"))
            return analyst

        analyst_callable = make_analyst(criteria_map, candidate, prior_rows, demand_rows)
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
        scorecard.clamp(final_evidence_set)

        for f in reinvestigation.flags:
            if f not in flags:
                flags.append(f)
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
                "evidence": [
                    {
                        "id": e.get("id"),
                        "url": e.get("url"),
                        "text": e.get("text"),
                        "content_hash": e.get("content_hash"),
                    }
                    for e in final_evidence_items
                ],
            }
        )

    # 5. Select protagonist
    protagonist, prot_reason = select_protagonist(evaluated_scorecards)

    protagonist_data = None
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

    # 6. Seal trace
    run_hash = trace.seal()

    # 7. Render JSON and Markdown
    results_json = {
        "date": now_iso,
        "run_id": active_run_id,
        "run_hash": run_hash,
        "command": command_str or " ".join(sys.argv),
        "llm_used": use_llm,
        "protagonist": protagonist_data,
        "candidates": candidates_output,
        "ineligible": ineligible_repos,
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(results_json, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Render Markdown
    md_content = _render_markdown(results_json)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_md.write_text(md_content, encoding="utf-8")

    return results_json


def _render_markdown(data: dict[str, Any]) -> str:
    lines = []
    lines.append("# Venture Scorecard & Protagonist Selection")
    lines.append("")
    lines.append(f"- **Date**: {data['date']}")
    lines.append(f"- **Run Hash**: `{data['run_hash']}`")
    lines.append(f"- **Command**: `{data['command']}`")
    lines.append(f"- **LLM Evaluator**: {'Live Nightshift' if data.get('llm_used') else 'Deterministic Baseline Analyst'}")
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

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Score candidate repos and select the demo protagonist.")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "real", help="Directory with discovery CSVs.")
    parser.add_argument("--output-json", type=Path, default=ROOT / "docs" / "revival" / "venture_scores.json")
    parser.add_argument("--output-md", type=Path, default=ROOT / "docs" / "revival" / "venture_scores.md")
    parser.add_argument("--max-calls", type=int, default=40, help="Toolbox max tool calls.")
    parser.add_argument("--max-seconds", type=float, default=300.0, help="Toolbox max runtime in seconds.")
    parser.add_argument("--llm", action="store_true", help="Use live Nightshift LLM evaluator.")
    parser.add_argument("--run-id", type=str, default=None, help="Custom trace run ID.")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "logs", help="Directory for trace logs.")
    args = parser.parse_args()

    results = score_all(
        data_dir=args.data_dir,
        output_json=args.output_json,
        output_md=args.output_md,
        max_calls=args.max_calls,
        max_seconds=args.max_seconds,
        use_llm=args.llm,
        run_id=args.run_id,
        log_dir=args.log_dir,
        command_str=" ".join(sys.argv),
    )

    prot = results.get("protagonist") or {}
    print(f"Run hash: {results.get('run_hash')}")
    if prot.get("repo_id"):
        print(f"Selected Protagonist: {prot['repo_id']} (Total: {prot.get('total')}, Feasibility: {prot.get('feasibility')})")
    else:
        print(f"No protagonist selected: {prot.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
