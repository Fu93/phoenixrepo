#!/usr/bin/env python3
"""Write the rescue plan (gate 2's PRD) for a CANDIDATE from its gate-1 scores file.

    python scripts/rescue_plan.py reports/<run_id>/scores.json
    python scripts/rescue_plan.py logs/zetaris-gate1.json --repo SergioMendolia/ytkb
    python scripts/rescue_plan.py reports/<run_id>/scores.json --no-llm      # deterministic plan

The scores file is what scripts/judge_repo.py or scripts/score_candidates.py wrote. The plan
needs no clone and no test command: it is built from the evidence gate 1 recorded, drafted by
Nemotron through Nightshift (the deep tier when one is configured), and every item is checked
against that evidence. Writes <out>/<owner>__<name>.rescue.md and .json, and a sealed trace
that scripts/verify_trace.py can check (add --require-nemotron to check the model).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.rescue_plan import build_plan, render_markdown, repo_tree, to_json  # noqa: E402
from app.trace_log import HackathonTrace  # noqa: E402


def candidate_rows(data: dict, repo: str | None) -> list[dict]:
    rows = [r for r in (data.get("candidates") or []) if isinstance(r, dict) and r.get("verdict") == "CANDIDATE"]
    if repo:
        rows = [r for r in rows if str(r.get("repo_id") or "").casefold() == repo.casefold()]
        if not rows:
            others = {str(r.get("repo_id")): str(r.get("verdict")) for r in (data.get("ineligible") or []) if isinstance(r, dict)}
            if repo in others:
                raise SystemExit(f"{repo} is {others[repo]} in this file; a rescue plan is written only for a CANDIDATE.")
            raise SystemExit(f"{repo} is not a CANDIDATE in this file.")
    if not rows:
        raise SystemExit("No CANDIDATE in this scores file; gate 1 found nothing to rescue.")
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scores", type=Path, help="scores JSON from judge_repo.py or score_candidates.py")
    ap.add_argument("--repo", default=None, help="owner/name when the file holds more than one CANDIDATE")
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / "rescue")
    ap.add_argument("--log-dir", type=Path, default=ROOT / "logs")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--no-llm", action="store_true", help="Deterministic plan from the same evidence; no model call.")
    ap.add_argument("--offline", action="store_true", help="Do not list the repository root on GitHub.")
    args = ap.parse_args(argv)

    data = json.loads(args.scores.read_text(encoding="utf-8"))
    rows = candidate_rows(data, args.repo)
    if len(rows) > 1 and not args.repo:
        names = ", ".join(str(r.get("repo_id")) for r in rows)
        raise SystemExit(f"This file holds {len(rows)} CANDIDATEs ({names}); pass --repo owner/name.")
    row = rows[0]
    repo_id = str(row["repo_id"])
    slug = repo_id.replace("/", "__")
    run_id = args.run_id or f"rescue-{slug.lower()}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"

    try:
        from config.settings import trace_secrets

        secrets = trace_secrets()
    except Exception:
        secrets = []
    trace = HackathonTrace(run_id, log_dir=args.log_dir, secrets=secrets)
    trace.event(agent_name="rescue_planner", action="RESCUE_PLAN_SOURCE", input_summary={"repo_id": repo_id},
                output_summary={"scores_file": args.scores.as_posix(), "gate1_run_id": data.get("run_id"), "gate1_run_hash": data.get("run_hash")})

    client = None
    if not args.no_llm:
        from scripts.revive import build_relevance_client

        client = build_relevance_client()
    tree = None if args.offline else repo_tree(repo_id)
    plan = build_plan(row, client=client, trace=trace, tree=tree, use_llm=not args.no_llm)
    plan["gate1"]["run_id"] = data.get("run_id")
    plan["gate1"]["run_hash"] = data.get("run_hash")
    run_hash = trace.seal()

    args.out.mkdir(parents=True, exist_ok=True)
    md = args.out / f"{slug}.rescue.md"
    js = args.out / f"{slug}.rescue.json"
    md.write_text(render_markdown(plan, run_id=run_id, run_hash=run_hash), encoding="utf-8")
    js.write_text(to_json({**plan, "run_id": run_id, "run_hash": run_hash}), encoding="utf-8")
    counts = ", ".join(f"{s['stage']} {len(s['items'])}" for s in plan["stages"])
    print(f"{repo_id}: drafted by {plan['drafted_by']}" + (f" ({plan['served_model']})" if plan["served_model"] else ""))
    print(f"items: {counts}; dropped by the check: {len(plan['rejected_items'])}; gate 2 can verify: {plan['organised']['status']}")
    print(f"plan: {md}\njson: {js}\ntrace: {trace.path}\nrun hash: {run_hash}")
    return 0


if __name__ == "__main__":
    from app.console import prepare_cli

    prepare_cli()
    raise SystemExit(main())
