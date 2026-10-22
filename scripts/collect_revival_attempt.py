"""Record one fork's revival-attempt rows and the sources a PRD may cite.

Uses the public GitHub REST API. GITHUB_TOKEN is optional and is never written.
Missing fields stay empty. Stage revive is the only row that can become success
or failure, and only when compare shows commits ahead of upstream. Refactor and
upgrade stay not_attempted until a later collection has a per-stage marker.
This script does not invent that marker from a commit message.

Run from the repo root:

  python scripts/collect_revival_attempt.py --fork Fu93/hygen --upstream jondot/hygen --issue 450
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from curate_real_repos import GitHubClient, select_build_ci, write_csv  # noqa: E402

from app.sql_catalog import ATTEMPT_COLUMNS, SOURCE_COLUMNS  # noqa: E402

STAGES = ("revive", "refactor", "upgrade")


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _flat(value: str, limit: int = 700) -> str:
    return " ".join(value.split())[:limit]


def _window(text: str, needles: tuple[str, ...], limit: int = 700) -> str:
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    for needle in needles:
        index = flat.find(needle)
        if index >= 0:
            start = max(0, index - 160)
            return flat[start : start + limit]
    return flat[:limit]


def _workflow_excerpt(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        flat = " ".join(line.split())
        if "node-version:" in flat and "matrix.node-version" not in flat and "[" in flat:
            kept.append(flat)
        elif "cron:" in flat:
            kept.append(flat)
    for needle in (
        "actions/checkout@v2",
        "actions/setup-node@v2",
        "cache: 'yarn'",
        "yarn install",
        "yarn build",
        "yarn hygen:build -- init self",
        "yarn test",
        "yarn test:win32",
    ):
        if needle in text and needle not in kept:
            kept.append(needle)
    return "; ".join(kept)


def _workflow_runs_build(text: str) -> str:
    excerpt = _workflow_excerpt(text) if text else ""
    if not excerpt:
        return ""
    lowered = excerpt.lower()
    if any(marker in lowered for marker in ("yarn test", "yarn build", "npm test", "npm run build")):
        return "true"
    return "false"


def _package_excerpt(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    scripts = payload.get("scripts") if isinstance(payload.get("scripts"), dict) else {}
    bin_field = payload.get("bin")
    if isinstance(bin_field, dict):
        bin_text = " ".join(f"{key} {value}" for key, value in bin_field.items())
    else:
        bin_text = _text(bin_field)
    dependencies = payload.get("dependencies") if isinstance(payload.get("dependencies"), dict) else {}
    parts = [
        f"version {_text(payload.get('version'))}",
        f"bin {bin_text}",
        f"scripts.test = {_text(scripts.get('test'))}",
        f"scripts.hygen:build = {_text(scripts.get('hygen:build'))}",
        f"scripts.build = {_text(scripts.get('build'))}",
        f"dependencies.ejs = {_text(dependencies.get('ejs'))}",
        f"dependencies.degit = {_text(dependencies.get('degit'))}",
    ]
    return "; ".join(part for part in parts if not part.endswith("= ") and not part.endswith(" "))


def collect_attempt(
    client: GitHubClient,
    *,
    fork: str,
    upstream: str,
    issue_number: int,
    collected_at: datetime,
) -> tuple[list[dict[str, str]], list[dict[str, str]], dict]:
    base = "https://api.github.com"
    origin = "github_rest_api:" + collected_at.date().isoformat()
    fork_payload, fork_status = client.get_json(f"{base}/repos/{fork}")
    runs_payload, runs_status = client.get_json(f"{base}/repos/{fork}/actions/runs?per_page=30")
    upstream_payload, upstream_status = client.get_json(f"{base}/repos/{upstream}")
    if not isinstance(fork_payload, dict) or fork_status != 200:
        raise SystemExit(f"fork repo request failed: {fork_status}")
    if not isinstance(upstream_payload, dict) or upstream_status != 200:
        raise SystemExit(f"upstream repo request failed: {upstream_status}")
    fork_branch = _text(fork_payload.get("default_branch"))
    upstream_branch = _text(upstream_payload.get("default_branch"))
    compare_url_api = f"{base}/repos/{upstream}/compare/{upstream_branch}...{fork.split('/')[0]}:{fork_branch}"
    compare_payload, compare_status = client.get_json(compare_url_api)
    issue_payload, issue_status = client.get_json(f"{base}/repos/{upstream}/issues/{issue_number}")
    issues_payload, issues_status = client.get_json(f"{base}/repos/{upstream}/issues?state=open&per_page=30")
    workflow_payload, workflow_status = client.get_json(
        f"{base}/repos/{upstream}/contents/.github/workflows/ci.yml?ref={upstream_branch}"
    )
    package_payload, package_status = client.get_json(
        f"{base}/repos/{upstream}/contents/package.json?ref={upstream_branch}"
    )
    workflow_text = _decode_content(workflow_payload) if workflow_status == 200 else ""
    package_json = _decode_json(package_payload) if package_status == 200 else None

    branch_runs = _runs_on_branch(runs_payload, fork_branch)
    conclusion, has_run, facts = select_build_ci({"workflow_runs": branch_runs}, fork)
    ahead = compare_payload.get("ahead_by") if isinstance(compare_payload, dict) else None
    behind = compare_payload.get("behind_by") if isinstance(compare_payload, dict) else None
    total_commits = compare_payload.get("total_commits") if isinstance(compare_payload, dict) else None
    status = _text(compare_payload.get("status")) if isinstance(compare_payload, dict) else ""
    base_sha = ""
    if isinstance(compare_payload, dict) and isinstance(compare_payload.get("base_commit"), dict):
        base_sha = _text(compare_payload["base_commit"].get("sha"))
    compare_html = ""
    if isinstance(compare_payload, dict):
        compare_html = _text(compare_payload.get("html_url")) or _text(compare_payload.get("permalink_url"))
    attempt_commit = isinstance(ahead, int) and ahead > 0
    ci_status = ""
    ci_run_url = ""
    ci_run_name = ""
    ci_run_event = ""
    commit_sha = ""
    revive_status = "not_attempted"
    if attempt_commit and has_run and conclusion == "success" and facts.get("html_url"):
        revive_status = "success"
        ci_status = "success"
        ci_run_url = facts.get("html_url", "")
        ci_run_name = facts.get("name", "")
        ci_run_event = facts.get("event", "")
        commit_sha = _head_sha(branch_runs, facts.get("id", ""))
    elif attempt_commit and has_run and conclusion in {"failure", "cancelled", "timed_out", "startup_failure"} and facts.get("html_url"):
        revive_status = "failure"
        ci_status = conclusion or ""
        ci_run_url = facts.get("html_url", "")
        ci_run_name = facts.get("name", "")
        ci_run_event = facts.get("event", "")
        commit_sha = _head_sha(branch_runs, facts.get("id", ""))
    elif attempt_commit and has_run and not conclusion:
        revive_status = "unknown"

    attempts = []
    for stage in STAGES:
        attempts.append(
            {
                "repo_id": upstream,
                "fork_full_name": fork,
                "upstream_full_name": upstream,
                "stage": stage,
                "status": revive_status if stage == "revive" else "not_attempted",
                "ci_status": ci_status if stage == "revive" else "",
                "ci_run_url": ci_run_url if stage == "revive" else "",
                "ci_run_name": ci_run_name if stage == "revive" else "",
                "ci_run_event": ci_run_event if stage == "revive" else "",
                "commit_sha": commit_sha if stage == "revive" else "",
                "compare_url": compare_html,
                "default_branch": fork_branch,
                "head_sha": base_sha,
                "upstream_base_sha": base_sha,
                "ci_head_repo": fork if commit_sha else "",
                "workflow_path": ".github/workflows/ci.yml" if workflow_text else "",
                "workflow_steps": _workflow_excerpt(workflow_text) if workflow_text else "",
                "workflow_runs_build": _workflow_runs_build(workflow_text),
                "data_origin": origin,
            }
        )

    issue_items = issues_payload if isinstance(issues_payload, list) else []
    issue_count = sum(1 for item in issue_items if isinstance(item, dict) and "pull_request" not in item)
    pull_count = sum(1 for item in issue_items if isinstance(item, dict) and "pull_request" in item)
    if not isinstance(issue_payload, dict) or issue_status != 200 or int(issue_payload.get("number") or 0) != issue_number:
        raise SystemExit(f"issue {issue_number} request failed: {issue_status}")
    issue_body = _text(issue_payload.get("body"))
    sources = [
        {
            "repo_id": upstream,
            "source_id": "workflow-ci",
            "kind": "workflow",
            "title": "CI",
            "url": f"https://github.com/{upstream}/blob/{upstream_branch}/.github/workflows/ci.yml",
            "excerpt": _workflow_excerpt(workflow_text),
            "data_origin": origin,
        },
        {
            "repo_id": upstream,
            "source_id": "package-json",
            "kind": "package",
            "title": "package.json",
            "url": f"https://github.com/{upstream}/blob/{upstream_branch}/package.json",
            "excerpt": _package_excerpt(package_json),
            "data_origin": origin,
        },
        {
            "repo_id": upstream,
            "source_id": f"issue-{issue_number}",
            "kind": "issue",
            "title": _text(issue_payload.get("title")),
            "url": _text(issue_payload.get("html_url")),
            "excerpt": _window(issue_body, ("Error: please specify a generator.", "Hygen v", "--version", "-v")),
            "data_origin": origin,
        },
        {
            "repo_id": upstream,
            "source_id": "issue-list",
            "kind": "issue_list",
            "title": "open issues first page",
            "url": f"https://github.com/{upstream}/issues",
            "excerpt": (
                f"GET /repos/{upstream}/issues?state=open&per_page=30 returned {len(issue_items)} items "
                f"on the first page ({issue_count} issues, {pull_count} pull requests). "
                f"Stage 3 uses issue {issue_number} only."
            ),
            "data_origin": origin,
        },
        {
            "repo_id": upstream,
            "source_id": "compare-fork",
            "kind": "compare",
            "title": f"compare {fork}",
            "url": compare_html,
            "excerpt": (
                f"status {status or 'empty'}; ahead_by {ahead if ahead is not None else 'empty'}; "
                f"behind_by {behind if behind is not None else 'empty'}; "
                f"total_commits {total_commits if total_commits is not None else 'empty'}; "
                f"base_sha {base_sha or 'empty'}"
            ),
            "data_origin": origin,
        },
        {
            "repo_id": upstream,
            "source_id": "fork-actions",
            "kind": "actions",
            "title": f"{fork} actions",
            "url": f"https://github.com/{fork}/actions",
            "excerpt": (
                f"default_branch {fork_branch or 'empty'}; "
                f"actions total_count {_total_count(runs_payload)}; "
                f"eligible_conclusion {conclusion or 'empty'}; "
                f"runs_on_default_branch {len(branch_runs)}"
            ),
            "data_origin": origin,
        },
    ]
    provenance = {
        "collected_at": collected_at.isoformat(),
        "generator": "scripts/collect_revival_attempt.py",
        "token_used": bool(client.token),
        "token_written": False,
        "fork": fork,
        "upstream": upstream,
        "fork_default_branch": fork_branch,
        "upstream_default_branch": upstream_branch,
        "fork_pushed_at": _text(fork_payload.get("pushed_at")),
        "issue_number": issue_number,
        "stage_rule": (
            "status success or failure is recorded on stage revive only when compare ahead_by is greater than 0 "
            "and the fork default branch has an eligible build/test run. Otherwise every stage is not_attempted. "
            "Refactor and upgrade are not inferred from commit messages. head_sha is the compare base SHA, not an attempt commit. "
            "commit_sha stays empty until an attempt commit is the head of an eligible run."
        ),
        "calls": client.calls,
        "statuses": {
            "fork": fork_status,
            "upstream": upstream_status,
            "runs": runs_status,
            "compare": compare_status,
            "issue": issue_status,
            "issues": issues_status,
            "workflow": workflow_status,
            "package": package_status,
        },
    }
    return attempts, sources, provenance


def _decode_content(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    if payload.get("encoding") == "base64" and isinstance(payload.get("content"), str):
        import base64

        return base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
    if isinstance(payload.get("content"), str) and payload.get("encoding") in {None, ""}:
        return payload["content"]
    return ""


def _decode_json(payload: object):
    text = _decode_content(payload)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _runs_on_branch(payload: object, branch: str) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        return []
    return [
        run
        for run in payload["workflow_runs"]
        if isinstance(run, dict) and _text(run.get("head_branch")) == branch
    ]


def _total_count(payload: object) -> str:
    if isinstance(payload, dict) and isinstance(payload.get("total_count"), int):
        return str(payload["total_count"])
    return "empty"


def _head_sha(runs: list[dict], run_id: str) -> str:
    for run in runs:
        if _text(run.get("id")) == run_id:
            return _text(run.get("head_sha"))
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Snapshot one fork revival attempt into data/real.")
    parser.add_argument("--fork", default="Fu93/hygen")
    parser.add_argument("--upstream", default="jondot/hygen")
    parser.add_argument("--issue", type=int, default=450)
    parser.add_argument("--output", default="data/real")
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    collected_at = datetime.now(timezone.utc)
    attempts, sources, provenance = collect_attempt(
        GitHubClient(token),
        fork=args.fork,
        upstream=args.upstream,
        issue_number=args.issue,
        collected_at=collected_at,
    )
    output = Path(args.output)
    write_csv(output / "revival_attempts.csv", ATTEMPT_COLUMNS, attempts)
    write_csv(output / "revival_sources.csv", SOURCE_COLUMNS, sources)
    (output / "revival_attempts_provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(attempts)} attempt rows and {len(sources)} sources to {output} at {provenance['collected_at']}")
    print("token was not written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
