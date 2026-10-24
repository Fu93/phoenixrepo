"""Plan-only PRD for a discovery candidate that passed the market filter.

This builder does not fork, commit, or invent roadmap work. Every factual line
quotes a discovery, prior-art, or demand field it was given.
"""

from __future__ import annotations

import json
from typing import Any

from app.agents.planner import WINDOW, prd_basename, slug
from app.sql_catalog import demand_sql, discovery_sql, prior_art_sql

__all__ = ["WINDOW", "build_discovery_prd", "prd_basename", "render_discovery_markdown", "render_json"]


def discovery_evidence_id(repo_id: str) -> str:
    return "EV-DISCOVERY-" + slug(repo_id)


def prior_evidence_id(repo_id: str) -> str:
    return "EV-PRIOR-" + slug(repo_id)


def demand_evidence_id(repo_id: str) -> str:
    return "EV-DEMAND-" + slug(repo_id)


def build_discovery_prd(
    candidate: dict[str, Any],
    prior_rows: list[dict[str, Any]],
    demand_rows: list[dict[str, Any]],
    queries: dict[str, dict[str, str]],
) -> dict[str, Any]:
    repo_id = str(candidate["repo_id"])
    discovery_q = _query(queries, "discovery", discovery_sql(repo_id))
    prior_q = _query(queries, "prior_art", prior_art_sql(repo_id))
    demand_q = _query(queries, "demand", demand_sql(repo_id))
    discovery_id = discovery_evidence_id(repo_id)
    prior_id = prior_evidence_id(repo_id)
    demand_id = demand_evidence_id(repo_id)
    paths = [part for part in str(candidate.get("code_paths") or "").split("|") if part]
    cited_paths = ", ".join(paths)
    idea = str(candidate.get("description") or "") + "\n" + str(candidate.get("readme_excerpt") or "")
    for phrase in (
        "go build -o ytkb",
        "go install",
        "Manage images and attachments",
        "Allow page creation from new md files",
        "Push changes back to YouTrack (update only, no creation)",
        "Download all pages from a YouTrack knowledge base, preserving hierarchy and order",
    ):
        if phrase not in idea:
            raise ValueError(f"{phrase!r} is not in the discovery README or description")

    findings = [
        _finding(
            (
                f"Problem cited from the description: {candidate.get('problem_excerpt')} "
                f"Target user cited from the README: {candidate.get('user_excerpt')} "
                f"Scope cited from the README: {candidate.get('scope_excerpt')}"
            ),
            discovery_id,
            discovery_q,
        ),
        _finding(
            (
                f"Code paths beyond a template: {cited_paths}. "
                f"Pushed {candidate.get('pushed_at')}, {candidate.get('days_since_push')} days before "
                f"{candidate.get('collected_at')}. Stars {candidate.get('stargazers_count')} are recorded "
                "and are not the demand signal. licence_spdx is "
                f"{candidate.get('licence_spdx') or 'empty'}."
            ),
            discovery_id,
            discovery_q,
        ),
    ]
    for row in demand_rows:
        parts = [f"Demand kind {row.get('kind')} signal {row.get('signal_id')}"]
        if row.get("url"):
            parts.append(f"url {row.get('url')}")
        if row.get("query"):
            parts.append(f"query {row.get('query')}")
        if row.get("metric_name"):
            parts.append(f"metric {row.get('metric_name')}={row.get('metric_value')}")
        if row.get("comments"):
            parts.append(f"comments {row.get('comments')}")
        if row.get("reactions"):
            parts.append(f"reactions {row.get('reactions')}")
        if row.get("excerpt"):
            parts.append(f"Excerpt: {row.get('excerpt')}")
        findings.append(_finding(" ".join(parts) + ".", demand_id, demand_q))
    for row in prior_rows:
        findings.append(
            _finding(
                (
                    f"Prior art {row.get('alternative_id')} ({row.get('url')}) licence "
                    f"{row.get('licence_spdx') or 'empty'}, stars {row.get('stars')}, pushed {row.get('pushed_at')}, "
                    f"{row.get('days_since_push')} days before collection. "
                    f"Required terms: {row.get('required_terms')}. Excerpt: {row.get('excerpt')}"
                ),
                prior_id,
                prior_q,
            )
        )

    revive = _task(
        "P1",
        (
            "Plan only. This run does not fork the repository and does not commit. "
            f"The tree already contains {cited_paths}. Last push {candidate.get('pushed_at')}."
        ),
        ["No fork and no commit are produced by this plan."],
        discovery_id,
        discovery_q,
    )
    refactor = _task(
        "P2",
        (
            "Plan only. Keep the source paths already in code_paths "
            f"({cited_paths}). This plan does not rewrite them."
        ),
        ["The plan names only paths present in code_paths."],
        discovery_id,
        discovery_q,
    )
    upgrade = _task(
        "P3",
        (
            "Plan only. The README excerpt records unchecked roadmap lines "
            "'Manage images and attachments' and 'Allow page creation from new md files'. "
            "Those lines are not scheduled by this plan."
        ),
        ["This plan does not add image upload or page creation."],
        discovery_id,
        discovery_q,
    )
    licence = _task(
        "P4",
        (
            "Add an SPDX licence file only after a human confirms the intended id. "
            f"licence_spdx is {candidate.get('licence_spdx') or 'empty'}. "
            "A README badge is not a licence file and is not licence_spdx."
        ),
        ["The licence file is added only after the SPDX id is confirmed. This plan does not invent one."],
        discovery_id,
        discovery_q,
    )
    install = _task(
        "P5",
        (
            "Keep the install path already in the README excerpt: `go build -o ytkb` and `go install`."
        ),
        ["The install instructions stay the commands quoted from the README excerpt."],
        discovery_id,
        discovery_q,
    )
    scope = _task(
        "P6",
        (
            "State the user-facing scope from the README features already cited: "
            "Download all pages from a YouTrack knowledge base, preserving hierarchy and order; "
            "Diff local files with server versions; "
            "Push changes back to YouTrack (update only, no creation); "
            "Tree display of the article hierarchy; "
            "Star to add or remove stars on articles."
        ),
        ["The user-facing scope lists those README features and does not add the unchecked roadmap items."],
        discovery_id,
        discovery_q,
    )
    stages = [
        {"id": "revive", "title": "Stage 1 — REVIVE", "gate_for_resurrected": False, "tasks": [revive]},
        {"id": "refactor", "title": "Stage 2 — REFACTOR", "gate_for_resurrected": False, "tasks": [refactor]},
        {"id": "upgrade", "title": "Stage 3 — UPGRADE", "gate_for_resurrected": False, "tasks": [upgrade]},
        {"id": "productise", "title": "Stage 4 — PRODUCTISE", "gate_for_resurrected": False, "tasks": [licence, install, scope]},
    ]
    problem = (
        f"Plan a product pass for {repo_id} ({candidate.get('html_url')}). "
        "Stages 1 to 3 are a plan only: this run does not fork or commit. "
        "Stage 4 states the licence gap, the install path already in the README, and the feature scope. "
        "This problem statement is task framing, not a cited evidence claim."
    )
    return {
        "kind": "discovery",
        "repo_id": repo_id,
        "drafted_by": "deterministic",
        "llm_drafted": False,
        "run_hash": "not sealed; this file is the deterministic snapshot, not an investigation run",
        "problem_framing": "non-factual",
        "out_of_scope_framing": "non-factual",
        "window": WINDOW,
        "problem": problem,
        "findings": [{"id": f"F{index + 1}", **finding} for index, finding in enumerate(findings)],
        "stages": stages,
        "tasks": [task for stage in stages for task in stage["tasks"]],
        "risks": [
            {
                "id": "R1",
                **_finding(
                    "A GitHub search total_count is that query's total. It is not a census and it does not prove that no other tool exists.",
                    demand_id,
                    demand_q,
                ),
            },
            {
                "id": "R2",
                **_finding(
                    "licence_spdx empty means the API sent no SPDX id. README badge text is not a substitute for that field.",
                    discovery_id,
                    discovery_q,
                ),
            },
            {
                "id": "R3",
                **_finding(
                    "This document is a plan. It does not record a fork, a commit, or a CI run for this repository.",
                    discovery_id,
                    discovery_q,
                ),
            },
        ],
        "out_of_scope": [
            "Do not fork this repository and do not push commits as part of this plan.",
            "Do not treat the repository's own stars, forks, or issues as demand.",
            "Do not schedule the unchecked README roadmap items.",
            "Do not treat a search total of 0 as proof that no alternative exists.",
            "Do not copy a README licence badge into licence_spdx.",
        ],
    }


def render_discovery_markdown(document: dict[str, Any]) -> str:
    lines = [
        f"# Discovery PRD: {document['repo_id']}",
        "",
        f"Drafted by: {document['drafted_by']}. llm_drafted: {str(document['llm_drafted']).lower()}.",
        f"Run hash: {document.get('run_hash') or 'not sealed; this file is the deterministic snapshot, not an investigation run'}",
        f"Window: {document['window']}",
        "",
        "## Problem statement",
        "",
        "Non-factual framing. This section sets the task. It is not a cited evidence claim.",
        "",
        document["problem"],
        "",
        "## Evidence-backed findings",
        "",
    ]
    for finding in document["findings"]:
        lines.append(f"- {finding['id']}: {finding['text']}")
        lines.append(f"  - {_citation(finding)}")
    for stage in document["stages"]:
        lines.extend(
            [
                "",
                f"## {stage['title']}",
                "",
                "Plan only. This run does not fork or commit.",
                "",
            ]
        )
        for task in stage["tasks"]:
            lines.append(f"### {task['id']}")
            lines.append("")
            lines.append(task["text"])
            lines.append("")
            lines.append("Acceptance:")
            for item in task["acceptance"]:
                lines.append(f"- {item}")
            for citation in task.get("citations") or [task]:
                lines.append(f"- {_citation(citation)}")
            lines.append("")
    lines.extend(["## Risks and unknowns", ""])
    for risk in document["risks"]:
        lines.append(f"- {risk['id']}: {risk['text']}")
        lines.append(f"  - {_citation(risk)}")
    lines.extend(
        [
            "",
            "## Out of scope",
            "",
            "Non-factual framing. These limits are instructions, not cited evidence claims.",
            "",
        ]
    )
    for item in document["out_of_scope"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def render_json(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _query(queries: dict[str, dict[str, str]], key: str, sql: str) -> dict[str, str]:
    supplied = queries.get(key) or {}
    return {
        "query_id": supplied.get("query_id") or "snapshot",
        "sql": supplied.get("sql") or sql,
        "content_hash": supplied.get("content_hash") or "",
    }


def _finding(text: str, evidence_id: str, query: dict[str, str]) -> dict[str, Any]:
    return {
        "text": text,
        "evidence_ids": [evidence_id],
        "query_id": query["query_id"],
        "sql": query["sql"],
        "content_hash": query["content_hash"],
    }


def _task(task_id: str, text: str, acceptance: list[str], evidence_id: str, query: dict[str, str]) -> dict[str, Any]:
    return {"id": task_id, "text": text, "acceptance": acceptance, **_finding(text, evidence_id, query)}


def _citation(item: dict[str, Any]) -> str:
    evidence = ", ".join(item.get("evidence_ids") or [])
    content_hash = item.get("content_hash") or ""
    hashed = f"; content_hash: {content_hash}" if content_hash else ""
    return f"evidence: {evidence}; query_id: {item.get('query_id')}; sql: {item.get('sql')}{hashed}"
