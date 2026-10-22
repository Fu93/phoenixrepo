"""Deterministic revival PRD.

A live model may draft the same shape. The critic rejects lines that do not cite
evidence ids from the Zetaris result. This builder quotes only row, signal,
attempt, and source fields it is given. It does not invent issues, commands, or
CI results.
"""

from __future__ import annotations

import json
from typing import Any

from app.sql_catalog import attempts_sql, profile_sql, signals_sql, sources_sql

STAGES = ("revive", "refactor", "upgrade")
WINDOW = "Finish each stage before 27 Oct 2026 23:45 UTC."


def slug(value: str) -> str:
    return "".join(char if char.isalnum() else "-" for char in value).strip("-")


def prd_basename(repo_id: str) -> str:
    return repo_id.replace("/", "__")


def profile_evidence_id(repo_id: str) -> str:
    return "EV-PROFILE-" + slug(repo_id)


def signal_evidence_id(repo_id: str, index: int, signal: str) -> str:
    return f"EV-SIGNAL-{slug(repo_id)}-{index}-{slug(signal)}"


def attempt_evidence_id(repo_id: str, stage: str) -> str:
    return f"EV-ATTEMPT-{slug(repo_id)}-{slug(stage or 'none')}"


def source_evidence_id(repo_id: str, source_id: str) -> str:
    return f"EV-SOURCE-{slug(repo_id)}-{slug(source_id or 'none')}"


def evidence_context(
    repo_id: str,
    signals: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    queries: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Stable evidence ids. Query ids default to snapshot when no live Zetaris result exists."""

    supplied = queries or {}

    def locator(name: str, sql: str) -> dict[str, str]:
        item = supplied.get(name) or {}
        return {"query_id": item.get("query_id") or "snapshot", "sql": item.get("sql") or sql}

    signal_items = []
    signal_loc = locator("signals", signals_sql(repo_id))
    for index, signal in enumerate(signals):
        signal_items.append(
            {
                "evidence_id": signal_evidence_id(repo_id, index, str(signal.get("signal") or "")),
                "signal": signal.get("signal") or "",
                "detail": signal.get("detail") or "",
                **signal_loc,
            }
        )
    attempt_items = []
    attempt_loc = locator("attempts", attempts_sql(repo_id))
    for attempt in attempts:
        attempt_items.append(
            {
                "evidence_id": attempt_evidence_id(repo_id, str(attempt.get("stage") or "")),
                "stage": attempt.get("stage") or "",
                **attempt_loc,
            }
        )
    if not attempt_items:
        attempt_items.append(
            {
                "evidence_id": attempt_evidence_id(repo_id, "none"),
                "stage": "",
                **attempt_loc,
            }
        )
    source_items = []
    source_loc = locator("sources", sources_sql(repo_id))
    for source in sources:
        source_items.append(
            {
                "evidence_id": source_evidence_id(repo_id, str(source.get("source_id") or "")),
                "source_id": source.get("source_id") or "",
                "kind": source.get("kind") or "",
                **source_loc,
            }
        )
    if not source_items:
        source_items.append(
            {
                "evidence_id": source_evidence_id(repo_id, "none"),
                "source_id": "",
                "kind": "",
                **source_loc,
            }
        )
    return {
        "profile": {
            "evidence_id": profile_evidence_id(repo_id),
            **locator("profile", profile_sql(repo_id)),
        },
        "signals": signal_items,
        "attempts": attempt_items,
        "sources": source_items,
    }


def _index(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for item in items:
        token = str(item.get(key) or "")
        if token:
            found[token] = item
    return found


def _line(
    text: str,
    evidence_ids: list[str],
    query_id: str,
    sql: str,
    *,
    acceptance: list[str] | None = None,
    citations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cited = citations or [{"evidence_ids": list(evidence_ids), "query_id": query_id, "sql": sql}]
    flat: list[str] = []
    for item in cited:
        for evidence_id in item.get("evidence_ids") or []:
            if evidence_id not in flat:
                flat.append(evidence_id)
    payload: dict[str, Any] = {
        "text": text,
        "evidence_ids": flat,
        "query_id": cited[0]["query_id"],
        "sql": cited[0]["sql"],
        "citations": cited,
    }
    if acceptance is not None:
        payload["acceptance"] = acceptance
    return payload


def _cite(ref: dict[str, Any]) -> dict[str, Any]:
    return {"evidence_ids": [ref["evidence_id"]], "query_id": ref["query_id"], "sql": ref["sql"]}


def _contains(text: str, needle: str) -> bool:
    return needle in (text or "")


def build_revival_prd(
    row: dict[str, Any],
    signals: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    repo_id = str(row["repo_id"])
    profile = evidence["profile"]
    signals_by_name = _index(evidence["signals"], "signal")
    attempts_by_stage = _index(evidence["attempts"], "stage")
    sources_by_id = _index(evidence["sources"], "source_id")
    sources_by_kind = _index(sources, "kind")
    attempt_rows = {str(item.get("stage") or ""): item for item in attempts}
    none_attempt = next((item for item in evidence["attempts"] if not item.get("stage")), evidence["attempts"][0])
    none_source = next((item for item in evidence["sources"] if not item.get("source_id")), evidence["sources"][0])
    workflow = sources_by_kind.get("workflow")
    package = sources_by_kind.get("package")
    issue = sources_by_kind.get("issue")
    issue_list = sources_by_kind.get("issue_list")
    compare = sources_by_kind.get("compare")
    actions = sources_by_kind.get("actions")
    ci_failure = next((item for item in signals if item.get("signal") == "ci_failure"), None)
    fork = ""
    branch = ""
    compare_url = ""
    head_sha = ""
    if attempts:
        fork = str(attempts[0].get("fork_full_name") or "")
        branch = str(attempts[0].get("default_branch") or "")
        compare_url = str(attempts[0].get("compare_url") or "")
        head_sha = str(attempts[0].get("head_sha") or "")

    findings: list[dict[str, Any]] = []
    findings.append(
        _line(
            (
                f"{repo_id} licence {row.get('licence_spdx') or 'missing'}, "
                f"days_since_push {row.get('days_since_push')}, "
                f"has_tests {row.get('has_tests')}, ci_status {row.get('ci_status') or 'empty'}, "
                f"description {row.get('description') or 'empty'}."
            ),
            [profile["evidence_id"]],
            profile["query_id"],
            profile["sql"],
        )
    )
    if ci_failure and "ci_failure" in signals_by_name:
        ref = signals_by_name["ci_failure"]
        findings.append(
            _line(
                f"Signal ci_failure: {ci_failure.get('detail')}",
                [ref["evidence_id"]],
                ref["query_id"],
                ref["sql"],
            )
        )
    for source in (workflow, package, issue, issue_list, compare, actions):
        if not source:
            continue
        ref = sources_by_id.get(str(source.get("source_id") or "")) or none_source
        findings.append(
            _line(
                (
                    f"Source {source.get('kind')} {source.get('source_id')} "
                    f"title {source.get('title') or 'empty'} url {source.get('url') or 'empty'}. "
                    f"Excerpt: {source.get('excerpt')}"
                ),
                [ref["evidence_id"]],
                ref["query_id"],
                ref["sql"],
            )
        )
    if not attempts:
        findings.append(
            _line(
                "phoenix.revival_attempts returned no rows. Revival has not yet been attempted.",
                [none_attempt["evidence_id"]],
                none_attempt["query_id"],
                none_attempt["sql"],
            )
        )
    else:
        for stage in STAGES:
            attempt = attempt_rows.get(stage)
            ref = attempts_by_stage.get(stage) or none_attempt
            if attempt is None:
                text = f"No revival_attempts row for stage {stage}."
            else:
                text = (
                    f"Stage {stage} status {attempt.get('status') or 'empty'} "
                    f"ci_status {attempt.get('ci_status') or 'empty'} "
                    f"ci_run_url {attempt.get('ci_run_url') or 'empty'} "
                    f"commit_sha {attempt.get('commit_sha') or 'empty'} "
                    f"head_sha {attempt.get('head_sha') or 'empty'} "
                    f"fork {attempt.get('fork_full_name') or 'empty'} "
                    f"default_branch {attempt.get('default_branch') or 'empty'} "
                    f"compare_url {attempt.get('compare_url') or 'empty'}."
                )
            findings.append(_line(text, [ref["evidence_id"]], ref["query_id"], ref["sql"]))

    revive_ref = attempts_by_stage.get("revive") or none_attempt
    workflow_excerpt = str((workflow or {}).get("excerpt") or "")
    workflow_ref = sources_by_id.get(str((workflow or {}).get("source_id") or "")) or none_source
    package_excerpt = str((package or {}).get("excerpt") or "")
    package_ref = sources_by_id.get(str((package or {}).get("source_id") or "")) or none_source
    issue_excerpt = str((issue or {}).get("excerpt") or "")
    issue_ref = sources_by_id.get(str((issue or {}).get("source_id") or "")) or none_source

    revive_tasks: list[dict[str, Any]] = []
    if _contains(workflow_excerpt, "14.x") or _contains(workflow_excerpt, "17.x"):
        needles = [token for token in ("14.x", "17.x") if _contains(workflow_excerpt, token)]
        citations = [_cite(workflow_ref)]
        if ci_failure and "toolchain setup step" in str(ci_failure.get("detail") or "").lower() and "ci_failure" in signals_by_name:
            citations.append(_cite(signals_by_name["ci_failure"]))
        missing = " and ".join(f"does not contain `{token}`" for token in needles)
        revive_tasks.append(
            {
                **_line(
                    (
                        "Stage 1 toolchain: the workflow excerpt lists Node "
                        + " and ".join(needles)
                        + ". The recorded CI blocker is a toolchain setup step, not a test failure. "
                        "Change the workflow so the eligible CI job no longer requests those Node versions. "
                        "Use one Node version actions/setup-node can install. Do not change product behavior in this stage."
                    ),
                    [],
                    "",
                    "",
                    citations=citations,
                    acceptance=[
                        f"On the fork default branch, `.github/workflows/ci.yml` {missing}.",
                    ],
                ),
                "id": "S1-toolchain",
                "stage": "revive",
            }
        )
    commands = [command for command in ("yarn install", "yarn build", "yarn test") if _contains(workflow_excerpt, command)]
    if commands:
        revive_tasks.append(
            {
                **_line(
                    "Stage 1 local gate: run the install, build, and test commands named in the workflow excerpt.",
                    [workflow_ref["evidence_id"]],
                    workflow_ref["query_id"],
                    workflow_ref["sql"],
                    acceptance=[f"`{command}` exits 0." for command in commands],
                ),
                "id": "S1-commands",
                "stage": "revive",
            }
        )
    fork_text = f"fork {fork} default branch {branch}" if fork and branch else "the fork default branch recorded on the attempt row"
    revive_tasks.append(
        {
            **_line(
                (
                    f"Stage 1 CI gate: push the attempt commit to {fork_text} and wait for an eligible "
                    "build/test workflow run on that default branch. Security scans, action_required runs, "
                    "and fork pull requests do not count. This stage is the only gate for RESURRECTED."
                ),
                [],
                "",
                "",
                citations=[_cite(revive_ref), _cite(profile)],
                acceptance=[
                    (
                        "phoenix.revival_attempts has a row for this repo_id with stage revive, "
                        "status success, ci_status success, a non-empty ci_run_url, and a non-empty commit_sha."
                    ),
                    "That ci_run_url is a build/test workflow run on the fork default branch, not a security scan.",
                ],
            ),
            "id": "S1-ci",
            "stage": "revive",
        }
    )

    refactor_tasks: list[dict[str, Any]] = []
    pins = [pin for pin in ("actions/checkout@v2", "actions/setup-node@v2") if _contains(workflow_excerpt, pin)]
    refactor_ref = attempts_by_stage.get("refactor") or none_attempt
    if pins:
        missing_pins = " and ".join(f"does not contain `{pin}`" for pin in pins)
        acceptance = [f"On the fork default branch, `.github/workflows/ci.yml` {missing_pins}."]
        if "yarn test" in commands:
            acceptance.append("`yarn test` exits 0.")
        acceptance.append(
            "phoenix.revival_attempts records stage refactor with status, ci_run_url, and commit_sha. "
            "That row does not change the verdict."
        )
        refactor_tasks.append(
            {
                **_line(
                    (
                        "Stage 2 refactor: the workflow excerpt pins "
                        + " and ".join(pins)
                        + ". Replace those action pins. Do not add a second product change in this stage."
                    ),
                    [],
                    "",
                    "",
                    citations=[_cite(workflow_ref), _cite(refactor_ref)],
                    acceptance=acceptance,
                ),
                "id": "S2-action-pins",
                "stage": "refactor",
            }
        )
    else:
        refactor_tasks.append(
            {
                **_line(
                    "Stage 2 refactor: no workflow excerpt names an obsolete action pin. Do not invent a cleanup.",
                    [none_source["evidence_id"]],
                    none_source["query_id"],
                    none_source["sql"],
                    acceptance=[
                        "phoenix.revival_attempts stage refactor stays not_attempted until a revival_sources excerpt names the pin to remove.",
                    ],
                ),
                "id": "S2-none",
                "stage": "refactor",
            }
        )

    upgrade_tasks: list[dict[str, Any]] = []
    upgrade_ref = attempts_by_stage.get("upgrade") or none_attempt
    version_error = "Error: please specify a generator."
    version_marker = "Hygen v"
    bin_token = "dist/bin.js"
    if issue and issue.get("url") and _contains(issue_excerpt, version_error) and _contains(issue_excerpt, version_marker) and _contains(package_excerpt, bin_token):
        acceptance = [
            f"`node {bin_token} --version` exits 0.",
            f"stdout contains `{version_marker}`.",
            f"stdout does not contain `{version_error}`",
        ]
        if _contains(issue_excerpt, "-v"):
            acceptance.append(f"`node {bin_token} -v` exits 0 and follows the same stdout checks.")
        acceptance.append(
            "phoenix.revival_attempts records stage upgrade with status, ci_run_url, and commit_sha. "
            "That row does not change the verdict."
        )
        upgrade_tasks.append(
            {
                **_line(
                    (
                        f"Stage 3 upgrade: {issue.get('title') or 'untitled issue'} at {issue.get('url')}. "
                        "The issue excerpt shows the version command printing the generator error before the version marker. "
                        "Change that command so the checks below pass. Do not take any other open issue in this stage."
                    ),
                    [],
                    "",
                    "",
                    citations=[_cite(issue_ref), _cite(package_ref), _cite(upgrade_ref)],
                    acceptance=acceptance,
                ),
                "id": "S3-issue",
                "stage": "upgrade",
            }
        )
    elif issue and issue.get("url"):
        upgrade_tasks.append(
            {
                **_line(
                    (
                        f"Stage 3 upgrade: {issue.get('title') or 'untitled issue'} at {issue.get('url')}. "
                        "The excerpt does not contain the version-command strings this builder knows how to check, "
                        "so no extra behavior is specified."
                    ),
                    [issue_ref["evidence_id"]],
                    issue_ref["query_id"],
                    issue_ref["sql"],
                    acceptance=[
                        "Do not implement a behavior that is not quoted in the issue excerpt.",
                        "phoenix.revival_attempts records stage upgrade with status, ci_run_url, and commit_sha. That row does not change the verdict.",
                    ],
                ),
                "id": "S3-issue",
                "stage": "upgrade",
            }
        )
    else:
        upgrade_tasks.append(
            {
                **_line(
                    "Stage 3 upgrade: no revival_sources row with kind issue was collected. Do not invent an issue or a feature.",
                    [none_source["evidence_id"]],
                    none_source["query_id"],
                    none_source["sql"],
                    acceptance=[
                        "No upgrade commit is made until phoenix.revival_sources has a kind issue row whose url is a real issue URL.",
                    ],
                ),
                "id": "S3-none",
                "stage": "upgrade",
            }
        )

    risks: list[dict[str, Any]] = []
    if compare is not None:
        ref = sources_by_id.get(str(compare.get("source_id") or "")) or none_source
        risks.append(
            _line(
                (
                    f"Compare source {compare.get('url') or 'empty'} excerpt: {compare.get('excerpt')}. "
                    "An empty commit_sha means there is no attempt commit yet. "
                    f"Observed head_sha is {head_sha or 'empty'} and is not an attempt commit."
                ),
                [ref["evidence_id"]],
                ref["query_id"],
                ref["sql"],
            )
        )
    if actions is not None:
        ref = sources_by_id.get(str(actions.get("source_id") or "")) or none_source
        risks.append(
            _line(
                f"Fork Actions source excerpt: {actions.get('excerpt')}. CI stays unknown until an eligible run exists.",
                [ref["evidence_id"]],
                ref["query_id"],
                ref["sql"],
            )
        )
    if ci_failure and "toolchain setup step" in str(ci_failure.get("detail") or "").lower():
        ref = signals_by_name["ci_failure"]
        risks.append(
            _line(
                "The upstream CI failure is a toolchain setup step. Do not describe it as a test failure and do not start by rewriting tests.",
                [ref["evidence_id"]],
                ref["query_id"],
                ref["sql"],
            )
        )
    risks.append(
        _line(
            "Stages refactor and upgrade are an upgrade result. Only stage revive success or failure changes RESURRECTED or RESURRECTION_FAILED. NO_GO is not overridden by a fork.",
            [profile["evidence_id"]],
            profile["query_id"],
            profile["sql"],
        )
    )
    risks.append(
        _line(
            WINDOW + " Keep each stage to the tasks in this PRD.",
            [profile["evidence_id"]],
            profile["query_id"],
            profile["sql"],
        )
    )

    issue_url = str((issue or {}).get("url") or "")
    out_of_scope = [
        "Do not invent GitHub issues, CI conclusions, commit SHAs, or test failures.",
        "Do not count a security or analysis scan, an action_required run, or a fork pull request as CI success.",
        "Do not publish a release or rewrite the product.",
        "Do not switch the package manager unless the workflow excerpt names that manager.",
    ]
    if issue_url:
        out_of_scope.append(f"Do not implement any open issue other than {issue_url}.")
    if commands and any(command.startswith("yarn") for command in commands):
        out_of_scope.append("Do not replace yarn with npm. The workflow excerpt names yarn.")

    target = f"https://github.com/{fork}" if fork else ""
    problem = (
        f"Produce an executable revival of {repo_id}"
        + (f" on {target} default branch {branch}" if target and branch else "")
        + ". Stage 1 REVIVE makes install, build, test, and eligible default-branch CI green. "
        "That stage is the gate for RESURRECTED. Stage 2 is one evidence-backed cleanup. "
        "Stage 3 is one evidence-backed visible upgrade. "
        + (f"Compare URL: {compare_url}. " if compare_url else "")
        + "No LLM drafted this text."
    )
    stages = [
        {
            "id": "revive",
            "title": "Stage 1 — REVIVE",
            "gate_for_resurrected": True,
            "tasks": revive_tasks,
        },
        {
            "id": "refactor",
            "title": "Stage 2 — REFACTOR / OPTIMISE",
            "gate_for_resurrected": False,
            "tasks": refactor_tasks,
        },
        {
            "id": "upgrade",
            "title": "Stage 3 — UPGRADE",
            "gate_for_resurrected": False,
            "tasks": upgrade_tasks,
        },
    ]
    flat_tasks = [task for stage in stages for task in stage["tasks"]]
    return {
        "repo_id": repo_id,
        "drafted_by": "deterministic",
        "llm_drafted": False,
        "window": WINDOW,
        "fork_full_name": fork,
        "default_branch": branch,
        "compare_url": compare_url,
        "problem": problem,
        "findings": [
            {"id": f"F{index + 1}", **finding} for index, finding in enumerate(findings)
        ],
        "stages": stages,
        "tasks": flat_tasks,
        "risks": [{"id": f"R{index + 1}", **risk} for index, risk in enumerate(risks)],
        "out_of_scope": out_of_scope,
    }


def render_markdown(document: dict[str, Any]) -> str:
    lines = [
        f"# Revival PRD: {document['repo_id']}",
        "",
        f"Drafted by: {document['drafted_by']}. llm_drafted: {str(document['llm_drafted']).lower()}.",
        f"Window: {document['window']}",
        "",
        "## Problem statement",
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
        gate = "Gate for RESURRECTED." if stage["gate_for_resurrected"] else "Upgrade result. Does not change the verdict."
        lines.extend(["", f"## {stage['title']}", "", gate, ""])
        for task in stage["tasks"]:
            lines.append(f"### {task['id']}")
            lines.append("")
            lines.append(task["text"])
            lines.append("")
            lines.append("Acceptance:")
            for item in task["acceptance"]:
                lines.append(f"- {item}")
            for citation in _citations(task):
                lines.append(f"- {_citation(citation)}")
            lines.append("")
    lines.extend(["## Risks and unknowns", ""])
    for risk in document["risks"]:
        lines.append(f"- {risk['id']}: {risk['text']}")
        lines.append(f"  - {_citation(risk)}")
    lines.extend(["", "## Out of scope", ""])
    for item in document["out_of_scope"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def render_json(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def _citations(item: dict[str, Any]) -> list[dict[str, Any]]:
    citations = item.get("citations")
    if citations:
        return citations
    return [{"evidence_ids": item.get("evidence_ids") or [], "query_id": item.get("query_id"), "sql": item.get("sql")}]


def _citation(item: dict[str, Any]) -> str:
    evidence = ", ".join(item.get("evidence_ids") or [])
    return f"evidence: {evidence}; query_id: {item.get('query_id')}; sql: {item.get('sql')}"
