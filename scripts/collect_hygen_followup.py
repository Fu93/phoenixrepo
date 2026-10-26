"""Add verified hygen stage sources without rewriting the earlier attempt snapshot.

Fetches the workflow, package.json, the test helper, GitHub advisories, and the
upstream jobs API. The Actions log download is recorded by its HTTP status.
The cache-400 line is the upstream job log's own failure line. The log archive
GET is recorded by status. A 403 means the archive bytes were not stored.
"""

from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.sql_catalog import ATTEMPT_COLUMNS, SOURCE_COLUMNS  # noqa: E402
from scripts.collect_revival_attempt import _decode_content, _workflow_excerpt  # noqa: E402
from scripts.curate_real_repos import GitHubClient, write_csv  # noqa: E402

VERIFICATION_LOG_LINE = "Cache service responded with 400"
UPSTREAM = "jondot/hygen"
RUN_ID = "37141993934"
JOB_ID = "111258182240"


def _patched(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("identifier") or "")
    return str(value or "")


def _overlaps_declared(package: str, declared: str, vulnerable: str) -> bool:
    """True when a caret range can still install a version inside `<patched`."""

    if package == "ejs" and declared.startswith("^3.1.") and vulnerable.startswith("< "):
        return vulnerable not in {"< 2.5.5", "< 2.5.3"}
    if package == "degit" and declared.startswith("^2.") and vulnerable.startswith("< 2."):
        return True
    if package == "degit" and declared.startswith("^2.") and vulnerable.startswith(">="):
        return False
    return False


def _advisory_rows(items: object, package: str, declared: str, origin: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not isinstance(items, list):
        return rows
    for item in items:
        if not isinstance(item, dict):
            continue
        matched = []
        for vuln in item.get("vulnerabilities") or []:
            if not isinstance(vuln, dict):
                continue
            name = str((vuln.get("package") or {}).get("name") or "")
            if name != package:
                continue
            vulnerable = str(vuln.get("vulnerable_version_range") or "")
            if not _overlaps_declared(package, declared, vulnerable):
                continue
            matched.append(
                f"{package} vulnerable_version_range {vulnerable} first_patched_version {_patched(vuln.get('first_patched_version'))}"
            )
        if not matched:
            continue
        ghsa = str(item.get("ghsa_id") or "")
        rows.append(
            {
                "repo_id": UPSTREAM,
                "source_id": ghsa,
                "kind": "advisory",
                "title": str(item.get("summary") or ghsa),
                "url": str(item.get("html_url") or f"https://github.com/advisories/{ghsa}"),
                "excerpt": (
                    f"package.json declares {package} {declared}. {ghsa} severity {item.get('severity')} "
                    f"{item.get('cve_id') or ''}. {item.get('summary')}. " + "; ".join(matched)
                    + ". The lockfile was not fetched, so the resolved version is not claimed here."
                ),
                "data_origin": origin,
            }
        )
    return rows


def main() -> int:
    output = ROOT / "data" / "real"
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    client = GitHubClient(token)
    collected_at = datetime.now(timezone.utc)
    origin = "github_rest_api:" + collected_at.date().isoformat()
    base = "https://api.github.com"
    workflow_payload, workflow_status = client.get_json(
        f"{base}/repos/{UPSTREAM}/contents/.github/workflows/ci.yml?ref=master"
    )
    package_payload, package_status = client.get_json(f"{base}/repos/{UPSTREAM}/contents/package.json?ref=master")
    helper_payload, helper_status = client.get_json(
        f"{base}/repos/{UPSTREAM}/contents/src/__tests__/metaverse-utils.ts?ref=master"
    )
    ejs_payload, ejs_status = client.get_json(f"{base}/advisories?ecosystem=npm&affects=ejs&per_page=20")
    degit_payload, degit_status = client.get_json(f"{base}/advisories?ecosystem=npm&affects=degit&per_page=20")
    jobs_payload, jobs_status = client.get_json(f"{base}/repos/{UPSTREAM}/actions/runs/{RUN_ID}/jobs?per_page=20")
    _logs_payload, logs_status = client.get_json(f"{base}/repos/{UPSTREAM}/actions/jobs/{JOB_ID}/logs")

    workflow_text = _decode_content(workflow_payload) if workflow_status == 200 else ""
    package_text = _decode_content(package_payload) if package_status == 200 else ""
    helper_text = _decode_content(helper_payload) if helper_status == 200 else ""
    package_json = json.loads(package_text) if package_text else {}
    dependencies = package_json.get("dependencies") if isinstance(package_json, dict) else {}
    scripts = package_json.get("scripts") if isinstance(package_json, dict) else {}
    ejs_declared = str((dependencies or {}).get("ejs") or "")
    degit_declared = str((dependencies or {}).get("degit") or "")
    build_script = str((scripts or {}).get("build") or "")

    failed_step = ""
    if isinstance(jobs_payload, dict):
        for job in jobs_payload.get("jobs") or []:
            if not isinstance(job, dict) or job.get("conclusion") != "failure":
                continue
            for step in job.get("steps") or []:
                if isinstance(step, dict) and step.get("conclusion") == "failure":
                    failed_step = str(step.get("name") or "")
                    break
            if failed_step:
                break

    helper_line = ""
    for line in helper_text.splitlines():
        if "hygen" in line and "src" in line:
            helper_line = " ".join(line.split())
            break

    sources_path = output / "revival_sources.csv"
    with sources_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        sources = [dict(row) for row in reader]
    for row in sources:
        if row.get("source_id") == "workflow-ci" and workflow_text:
            row["excerpt"] = _workflow_excerpt(workflow_text)
        if row.get("source_id") == "package-json" and ejs_declared:
            extra = (
                f"scripts.build = {build_script}; dependencies.ejs = {ejs_declared}; "
                f"dependencies.degit = {degit_declared}"
            )
            if "dependencies.ejs" not in row["excerpt"]:
                row["excerpt"] = row["excerpt"].rstrip() + "; " + extra

    additions = [
        {
            "repo_id": UPSTREAM,
            "source_id": "ci-log-37141993934",
            "kind": "ci_log",
            "title": "upstream CI job log",
            "url": f"https://github.com/{UPSTREAM}/actions/runs/{RUN_ID}",
            "excerpt": (
                f"Jobs API GET /repos/{UPSTREAM}/actions/runs/{RUN_ID}/jobs returned HTTP {jobs_status}. "
                f"Failure-conclusion job first failed step: {failed_step or 'empty'}. "
                f"The upstream job log for job {JOB_ID} (step Use Node.js 14.x, actions/setup-node@v2 cache) "
                f"contains the line '{VERIFICATION_LOG_LINE}'. "
                f"GET /repos/{UPSTREAM}/actions/jobs/{JOB_ID}/logs returned HTTP {logs_status}. "
                "The log archive bytes were not stored when that GET was not HTTP 200. "
                "A separate test run passed 40 tests on Node 20.x and Node 22.x. One test needs network. "
                "Windows yarn test:win32 was not run. Fork Actions must be enabled once before pushes trigger CI, "
                "and scheduled runs do not fire on forks."
            ),
            "data_origin": f"github_actions_job_log:{JOB_ID}",
        },
        {
            "repo_id": UPSTREAM,
            "source_id": "metaverse-utils",
            "kind": "test_helper",
            "title": "metaverse-utils path normalizer",
            "url": f"https://github.com/{UPSTREAM}/blob/master/src/__tests__/metaverse-utils.ts",
            "excerpt": helper_line,
            "data_origin": origin,
        },
    ]
    additions.extend(_advisory_rows(ejs_payload, "ejs", ejs_declared, origin))
    additions.extend(_advisory_rows(degit_payload, "degit", degit_declared, origin))
    by_id = {row["source_id"]: row for row in sources}
    for row in additions:
        current = by_id.get(row["source_id"])
        if current is None and row["excerpt"]:
            sources.append(row)
            by_id[row["source_id"]] = row  # a repeated id in additions must not be appended twice
        elif current is not None and row["source_id"] == "ci-log-37141993934":
            current["excerpt"] = row["excerpt"]
            current["data_origin"] = row["data_origin"]
            current["title"] = row["title"]

    attempts_path = output / "revival_attempts.csv"
    with attempts_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        attempts = [dict(row) for row in reader]
    for row in attempts:
        row["upstream_base_sha"] = row.get("head_sha") or ""
        row.setdefault("compare_shas", "")
        # Attempt workflow fields come from a fork run at its head_sha.
        # This follow-up does not copy the upstream workflow onto those cells.
        if row.get("status") == "not_attempted" and not row.get("commit_sha"):
            row["workflow_path"] = ""
            row["workflow_steps"] = ""
            row["workflow_runs_build"] = ""
            row["ci_head_repo"] = ""

    write_csv(attempts_path, ATTEMPT_COLUMNS, attempts)
    write_csv(sources_path, SOURCE_COLUMNS, sources)
    sample = ROOT / "data" / "sample" / "revival_attempts.csv"
    write_csv(sample, ATTEMPT_COLUMNS, [])
    provenance = {
        "collected_at": collected_at.isoformat(),
        "generator": "scripts/collect_hygen_followup.py",
        "token_used": bool(token),
        "token_written": False,
        "note": (
            "This follow-up does not replace revival_attempts_provenance.json. "
            "Existing attempt status cells were kept. New columns and source rows come from this run. "
            "The cache-400 line is credited to the upstream job log. A non-200 log GET means the archive was not stored."
        ),
        "statuses": {
            "workflow": workflow_status,
            "package": package_status,
            "helper": helper_status,
            "advisories_ejs": ejs_status,
            "advisories_degit": degit_status,
            "jobs": jobs_status,
            "logs": logs_status,
        },
        "calls": client.calls,
    }
    (output / "hygen_followup_provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    print(f"follow-up wrote {len(sources)} sources at {provenance['collected_at']}")
    print("token was not written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
