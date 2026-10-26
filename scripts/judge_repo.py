#!/usr/bin/env python3
"""phoenix judge <github-url>: understand a repository, search the whole web, verify quotes, give a verdict.

One command, one product line: it fetches the repository, builds a one-row discovery snapshot, runs
the same scoring pipeline as the batch run (problem extraction, multi-source prior-art search,
LLM relevance with verbatim quote checks, mainstream-overlap guard), and writes a one-page HTML
evidence report. It never uses the repository's own stars or forks as evidence of demand.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.sql_catalog import DISCOVERY_COLUMNS  # noqa: E402
from scripts import collect_discovery as cd  # noqa: E402
from scripts import evidence_report, score_candidates  # noqa: E402

PRIOR_ART_HEADER = "repo_id,alternative_id,url,licence_spdx,stars,pushed_at,days_since_push,excerpt,candidate_excerpt,required_terms,data_origin,collected_at\n"
DEMAND_HEADER = "repo_id,signal_id,kind,url,excerpt,metric_name,metric_value,comments,reactions,stars,pushed_at,days_since_push,query,data_origin,collected_at\n"

_URL_RE = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?(?:[#?].*)?$")


class JudgeError(RuntimeError):
    """The repository could not be turned into a judgeable row. The message says why."""


def parse_repo_url(url: str) -> str:
    m = _URL_RE.match(url.strip())
    if m:
        return f"{m.group(1)}/{m.group(2)}"
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", url.strip()):
        return url.strip()
    raise JudgeError(f"not a GitHub repository URL: {url!r}")


def _get(url: str, token: str, accept: str = "application/vnd.github+json") -> Any:
    headers = {"Accept": accept, "User-Agent": "phoenixrepo-judge"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise JudgeError(f"GitHub returned HTTP {exc.code} for {url.split('?')[0]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise JudgeError(f"could not reach GitHub: {exc}") from exc
    return body.decode("utf-8", errors="replace") if accept.endswith("raw") else json.loads(body)


def fetch_repo(full_name: str, token: str | None = None, getter=_get) -> dict[str, Any]:
    token = cd.get_github_token() if token is None else token
    repo = getter(f"https://api.github.com/repos/{full_name}", token)
    try:
        readme = getter(f"https://api.github.com/repos/{full_name}/readme", token, "application/vnd.github.raw")
    except JudgeError:
        readme = ""
    branch = repo.get("default_branch") or "HEAD"
    try:
        tree = getter(f"https://api.github.com/repos/{full_name}/git/trees/{branch}?recursive=1", token)
        paths = [t["path"] for t in tree.get("tree", []) if t.get("type") == "blob"]
    except JudgeError:
        paths = []
    return {"repo": repo, "readme": readme, "paths": paths}


def build_row(fetched: dict[str, Any], collected_at: str) -> dict[str, str]:
    repo = fetched["repo"]
    if not str(repo.get("description") or "").strip():
        raise JudgeError("the repository has no description, so there is no stated problem to judge")
    feats = cd.extract_candidate_features(repo, fetched["readme"], fetched["paths"], collected_at)
    if feats is None:
        raise JudgeError("not enough source files or readable feature descriptions to judge this repository")
    days = cd._days(repo.get("pushed_at"), collected_at)
    lic = (repo.get("license") or {}).get("spdx_id") or ""
    row = {
        "repo_id": feats["repo_id"],
        "html_url": repo.get("html_url") or f"https://github.com/{feats['repo_id']}",
        "description": str(repo.get("description") or "").strip(),
        "readme_excerpt": feats["readme_excerpt"],
        "problem_excerpt": feats["problem_excerpt"],
        "user_excerpt": feats["user_excerpt"],
        "scope_excerpt": feats["scope_excerpt"],
        "code_paths": "|".join(feats["code_paths"]),
        "created_at": repo.get("created_at") or "",
        "pushed_at": repo.get("pushed_at") or "",
        "days_since_push": str(days),
        "stargazers_count": str(repo.get("stargazers_count", 0)),
        "forks_count": str(repo.get("forks_count", 0)),
        "open_issues": str(repo.get("open_issues_count", 0)),
        "licence_spdx": lic if lic != "NOASSERTION" else "",
        "core_terms": "|".join(feats["core_terms"]),
        "cohort": "discovery",
        "data_origin": f"github_rest_api:{collected_at}",
        "collected_at": collected_at,
    }
    return row


def write_snapshot(row: dict[str, str], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "discovery_candidates.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(DISCOVERY_COLUMNS), lineterminator="\n")
        w.writeheader()
        w.writerow({k: row.get(k, "") for k in DISCOVERY_COLUMNS})
    (directory / "prior_art.csv").write_text(PRIOR_ART_HEADER, encoding="utf-8", newline="\n")
    (directory / "demand_signals.csv").write_text(DEMAND_HEADER, encoding="utf-8", newline="\n")
    (directory / "exclusions.json").write_text("{}\n", encoding="utf-8", newline="\n")
    (directory / "discovery_provenance.json").write_text(
        json.dumps({"collected_at": row["collected_at"], "data_origin": row["data_origin"], "token_used": False, "token_written": False, "commands": ["phoenix judge"]}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return directory


def judge(url: str, out_dir: Path | None = None, use_llm: bool = True, run_id: str | None = None, log_dir: Path | None = None, getter=_get, scorer=None) -> dict[str, Any]:
    """Judge one repository. Returns {repo_id, verdict, reason, run_id, run_hash, report}."""
    full_name = parse_repo_url(url)
    collected_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    fetched = fetch_repo(full_name, getter=getter)
    row = build_row(fetched, collected_at)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    rid = run_id or f"judge-{stamp}"
    logs = log_dir or (ROOT / "logs")
    out = out_dir or (ROOT / "reports" / rid)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="phoenix-judge-") as tmp:
        snap = write_snapshot(row, Path(tmp))
        score_kwargs = dict(
            snapshot=snap,
            output_json=out / "scores.json",
            output_md=out / "scores.md",
            use_llm=use_llm,
            run_id=rid,
            log_dir=logs,
            command_str=f"phoenix judge {url}",
        )
        if scorer is None:
            from app.agents.repo_facts import RepoFactsClient

            score_kwargs["facts_client"] = RepoFactsClient(cache_dir=snap / ".cache" / "repo_facts")
            results = score_candidates.score_all(**score_kwargs)
        else:
            results = scorer(**score_kwargs)
    repo_id = row["repo_id"]
    verdict, reason = "NEEDS_REVIEW", "no verdict was recorded"
    for c in results.get("candidates") or []:
        if c.get("repo_id") == repo_id:
            verdict, reason = "CANDIDATE", str(c.get("reason") or "all guards passed")
    for r in results.get("ineligible") or []:
        if r.get("repo_id") == repo_id:
            verdict, reason = str(r.get("verdict")), str(r.get("reason") or "")
    events = evidence_report.load_events(logs, run_ids=[rid])
    report = out / "report.html"
    report.write_text(
        evidence_report.render_repo_html(
            repo_id,
            verdict,
            reason,
            evidence_report.events_for_repo(events, repo_id),
            run_id=rid,
            run_hash=str(results.get("run_hash") or ""),
            model_rows=evidence_report.served_model_rows(events),
        ),
        encoding="utf-8",
    )
    return {"repo_id": repo_id, "verdict": verdict, "reason": reason, "run_id": rid, "run_hash": results.get("run_hash"), "report": str(report)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", help="GitHub repository URL or owner/name")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--no-llm", action="store_true", help="Plumbing test only: skip the LLM judgement.")
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()
    try:
        res = judge(args.url, out_dir=args.out, use_llm=not args.no_llm, run_id=args.run_id)
    except JudgeError as exc:
        sys.stderr.write(f"cannot judge: {exc}\n")
        return 2
    except score_candidates.RelevanceLLMError as exc:
        sys.stderr.write(f"judging aborted: {exc}\n")
        return 1
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    from app.console import ensure_utf8_stdio

    ensure_utf8_stdio()
    raise SystemExit(main())