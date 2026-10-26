"""Normalize the hygen upgrade evidence into one snapshot.

The raw files are command output and GitHub payloads. This script does not
call the network. ``build_upgrade_prd`` in ``app.agents.upgrade_planner`` is
what turns the snapshot into the upgrade plan.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RAW = ROOT / "data" / "upgrade" / "jondot__hygen" / "raw"
SNAPSHOT = ROOT / "data" / "upgrade" / "jondot__hygen" / "snapshot.json"


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_ndjson(payload: str) -> list[dict[str, Any]]:
    rows = []
    for line in payload.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def parse_outdated(payload: str) -> list[dict[str, str]]:
    rows = []
    for item in _load_ndjson(payload):
        if item.get("type") != "table":
            continue
        for body in item["data"]["body"]:
            package, current, wanted, latest, package_type, url = body
            rows.append(
                {
                    "package": package,
                    "current": current,
                    "wanted": wanted,
                    "latest": latest,
                    "package_type": package_type,
                    "url": url,
                }
            )
    if not rows:
        raise SystemExit("yarn outdated payload has no table")
    return rows


def _has_major(spec: str, major: int) -> bool:
    for token in str(spec).split():
        head = token.lstrip("<>=~^").split(".", 1)[0]
        if head.isdigit() and int(head) == major:
            return True
    return False


def reduce_audit(payload: str) -> dict[str, Any]:
    """Collapse a ``yarn audit --json`` stream to the counts the plan cites."""

    summary = None
    advisory_rows = 0
    high_critical_by_root: dict[str, int] = {}
    direct: list[dict[str, Any]] = []
    js_yaml_4x: dict[tuple[str, str, str], str] = {}
    for item in _load_ndjson(payload):
        if item.get("type") == "auditSummary":
            summary = item["data"]
            continue
        if item.get("type") != "auditAdvisory":
            continue
        advisory_rows += 1
        data = item["data"]
        advisory = data["advisory"]
        resolution = data.get("resolution") or {}
        path = str(resolution.get("path") or "")
        root = path.split(">")[0] if path else ""
        severity = str(advisory.get("severity") or "")
        if severity in ("high", "critical") and root:
            high_critical_by_root[root] = high_critical_by_root.get(root, 0) + 1
        if path and ">" not in path and advisory.get("module_name") == root:
            direct.append(
                {
                    "id": advisory.get("id"),
                    "ghsa": advisory.get("github_advisory_id"),
                    "module": advisory.get("module_name"),
                    "severity": severity,
                    "vulnerable": advisory.get("vulnerable_versions"),
                    "patched": advisory.get("patched_versions"),
                    "title": advisory.get("title"),
                    "path": path,
                }
            )
        module = advisory.get("module_name")
        vulnerable = str(advisory.get("vulnerable_versions") or "")
        if module == "js-yaml" and _has_major(vulnerable, 4):
            key = (
                str(advisory.get("github_advisory_id") or ""),
                vulnerable,
                str(advisory.get("patched_versions") or ""),
            )
            js_yaml_4x.setdefault(key, path)
    if summary is None:
        raise SystemExit("yarn audit payload has no auditSummary")
    return {
        "advisory_rows": advisory_rows,
        "summary": summary,
        "high_critical_by_root": dict(sorted(high_critical_by_root.items())),
        "direct_advisories": sorted(direct, key=lambda item: (str(item["module"]), str(item["ghsa"]))),
        "js_yaml_4x": [
            {"ghsa": ghsa, "vulnerable": vulnerable, "patched": patched, "example_path": path}
            for (ghsa, vulnerable, patched), path in sorted(js_yaml_4x.items())
        ],
    }


def parse_prod_audit(payload: str) -> dict[str, Any]:
    summary = None
    advisories = []
    for item in _load_ndjson(payload):
        if item.get("type") == "auditSummary":
            summary = item["data"]
            continue
        if item.get("type") != "auditAdvisory":
            continue
        advisory = item["data"]["advisory"]
        resolution = item["data"].get("resolution") or {}
        advisories.append(
            {
                "id": advisory.get("id"),
                "ghsa": advisory.get("github_advisory_id"),
                "module": advisory.get("module_name"),
                "severity": advisory.get("severity"),
                "vulnerable": advisory.get("vulnerable_versions"),
                "patched": advisory.get("patched_versions"),
                "title": advisory.get("title"),
                "path": resolution.get("path"),
                "dev": resolution.get("dev"),
            }
        )
    if summary is None:
        raise SystemExit("production audit payload has no auditSummary")
    return {"summary": summary, "advisories": advisories}


def parse_issues(payload: list[dict[str, Any]]) -> dict[str, Any]:
    issues = []
    pull_requests = 0
    for item in payload:
        if "pull_request" in item:
            pull_requests += 1
            continue
        reactions = item.get("reactions") or {}
        issues.append(
            {
                "number": item["number"],
                "title": item.get("title") or "",
                "html_url": item.get("html_url") or "",
                "comments": int(item.get("comments") or 0),
                "reactions_total": int(reactions.get("total_count") or 0),
                "reactions_plus_one": int(reactions.get("+1") or 0),
                "updated_at": item.get("updated_at") or "",
                "body": item.get("body") or "",
            }
        )
    return {"open_issues": len(issues), "open_pull_requests": pull_requests, "issues": issues}


def parse_workflow(payload: str) -> dict[str, Any]:
    lines = []
    for number, line in enumerate(payload.splitlines(), start=1):
        if any(
            needle in line
            for needle in (
                "node-version:",
                "os:",
                "test:win32",
                "yarn test",
                "yarn build",
                "windows",
                "hygen",
                "checkout@",
                "setup-node@",
            )
        ):
            lines.append({"line": number, "text": line.rstrip("\n")})
    return {"path": ".github/workflows/ci.yml", "lines": lines}


def parse_ci(payload: dict[str, Any]) -> dict[str, Any]:
    run = payload["run"]
    jobs = []
    for job in payload["jobs"]:
        jobs.append(
            {
                "name": job["name"],
                "conclusion": job["conclusion"],
                "steps": [{"name": step["name"], "conclusion": step["conclusion"]} for step in job["steps"]],
            }
        )
    return {
        "id": run["id"],
        "html_url": run["html_url"],
        "name": run["name"],
        "event": run["event"],
        "status": run["status"],
        "conclusion": run["conclusion"],
        "head_branch": run["head_branch"],
        "head_sha": run["head_sha"],
        "jobs": jobs,
    }


def assemble(raw: Path) -> dict[str, Any]:
    meta = json.loads((raw / "provenance.json").read_text(encoding="utf-8"))
    file_hashes = {
        "outdated.ndjson": sha256_bytes((raw / "outdated.ndjson").read_bytes()),
        "audit-prod.ndjson": sha256_bytes((raw / "audit-prod.ndjson").read_bytes()),
        "audit-full.reduced.json": sha256_bytes((raw / "audit-full.reduced.json").read_bytes()),
        "issues.json": sha256_bytes((raw / "issues.json").read_bytes()),
    }
    for name, digest in file_hashes.items():
        expected = meta["files"][name]["sha256"]
        if expected != digest:
            raise SystemExit(f"{name} sha256 {digest} does not match provenance {expected}")
    reduced = json.loads((raw / "audit-full.reduced.json").read_text(encoding="utf-8"))
    if reduced.get("source_sha256") != meta["commands"]["audit_full"]["sha256"]:
        raise SystemExit("reduced audit source_sha256 does not match the full audit command")
    if reduced.get("source_bytes") != meta["commands"]["audit_full"]["bytes"]:
        raise SystemExit("reduced audit source_bytes does not match the full audit command")
    evidence = {
        "EV-PROVENANCE": meta,
        "EV-PACKAGE": json.loads((raw / "package.json").read_text(encoding="utf-8")),
        "EV-OUTDATED": {"rows": parse_outdated((raw / "outdated.ndjson").read_text(encoding="utf-8"))},
        "EV-AUDIT-FULL": json.loads((raw / "audit-full.reduced.json").read_text(encoding="utf-8")),
        "EV-AUDIT-PROD": parse_prod_audit((raw / "audit-prod.ndjson").read_text(encoding="utf-8")),
        "EV-CI": parse_ci(json.loads((raw / "ci.json").read_text(encoding="utf-8"))),
        "EV-WORKFLOW": parse_workflow((raw / "ci.yml").read_text(encoding="utf-8")),
        "EV-ISSUES": parse_issues(json.loads((raw / "issues.json").read_text(encoding="utf-8"))),
        "EV-SNIPPETS": json.loads((raw / "snippets.json").read_text(encoding="utf-8")),
        "EV-PR": json.loads((raw / "pull.json").read_text(encoding="utf-8")),
    }
    return {
        "repo_id": "jondot/hygen",
        "fork": meta["fork"],
        "commit": meta["commit"],
        "agent_branch": meta["agent_branch"],
        "collected_at": meta["collected_at"],
        "evidence": evidence,
        "content_hashes": {key: content_hash(value) for key, value in evidence.items()},
    }


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--reduce-audit":
        source = Path(sys.argv[2])
        reduced = reduce_audit(source.read_text(encoding="utf-8"))
        reduced["source_sha256"] = sha256_bytes(source.read_bytes())
        reduced["source_bytes"] = source.stat().st_size
        destination = Path(sys.argv[3])
        destination.write_text(json.dumps(reduced, indent=2) + "\n", encoding="utf-8")
        print(destination)
        return 0
    snapshot = assemble(RAW)
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    print(SNAPSHOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
