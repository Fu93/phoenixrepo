#!/usr/bin/env python3
"""phoenix revive <github-url>: gate 1, then a real build-and-verify loop on a local clone.

Gate 1 runs first (the same judge as `phoenix judge`). The revival loop then runs the repository's
own tests, lets the model patch the code, re-runs the tests and sends real failure output back.
It commits to a local branch only. It never pushes and never opens a pull request.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import revive_loop  # noqa: E402
from app.agents.relevance import RelevanceClient  # noqa: E402
from app.revive_prd import write_reports  # noqa: E402
from app.trace_log import HackathonTrace  # noqa: E402
from scripts import judge_repo  # noqa: E402


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url")
    ap.add_argument("--workdir", type=Path, default=ROOT / "work")
    ap.add_argument("--iterations", type=int, default=4)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--gate-verdict", default=None, help="Reuse an earlier gate-1 verdict instead of re-judging (CANDIDATE, ALREADY_EXISTS, ...).")
    ap.add_argument("--time-budget", type=float, default=2400, dest="time_budget", help="Stop starting new upgrade or refactor items after this many seconds (default 2400).")
    return ap


def main() -> int:
    args = build_parser().parse_args()

    full_name = judge_repo.parse_repo_url(args.url)
    rid = args.run_id or "revive-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    out = ROOT / "reports" / rid
    out.mkdir(parents=True, exist_ok=True)
    trace = HackathonTrace(rid, ROOT / "logs")

    verdict = args.gate_verdict
    if verdict is None:
        gate = judge_repo.judge(args.url, run_id=rid + "-gate1")
        verdict = gate["verdict"]
        trace.event(agent_name="market_judge", action="GATE1", input_summary=full_name, output_summary={"verdict": verdict, "reason": gate["reason"][:300], "gate_run": gate["run_id"], "gate_hash": gate.get("run_hash")})
    else:
        trace.event(agent_name="market_judge", action="GATE1", input_summary=full_name, output_summary={"verdict": verdict, "source": "supplied"})

    if verdict != "CANDIDATE":
        # Do not even clone: gate 1 refused (or could not judge) this repository.
        result = revive_loop.revive(Path("."), None, trace, full_name, gate_verdict=verdict)
        trace.event(agent_name="orchestrator", action="REVIVE_RESULT", input_summary=full_name, output_summary={"state": result.state, "reason": result.reason, "gate1": verdict})
        trace.seal()
        payload = result.as_dict() | {"repo": full_name, "gate1": verdict, "run_id": rid, "run_hash": trace.run_hash}
        write_reports(out, result, full_name, rid)
        (out / "revive.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        emit_result(payload, ("repo", "gate1", "state", "reason", "run_id"))
        return 3

    target = args.workdir / full_name.replace("/", "__")
    if target.exists():
        sys.stderr.write(f"{target} already exists; remove it or pick another --workdir\n")
        return 2
    args.workdir.mkdir(parents=True, exist_ok=True)
    _git(["clone", "--depth", "1", f"https://github.com/{full_name}.git", str(target)], args.workdir)
    _git(["checkout", "-q", "-b", f"phoenix/{rid}"], target)

    description = ""
    try:
        description = str(judge_repo.fetch_repo(full_name)["repo"].get("description") or "")
    except Exception:  # noqa: BLE001
        pass
    client = RelevanceClient()
    result = revive_loop.revive(target, client, trace, full_name, description=description, gate_verdict=verdict, max_iterations=args.iterations, time_budget_s=args.time_budget)

    if result.files_changed:
        _git(["add", "-A"], target)
        _git(["-c", "user.name=PhoenixRepo", "-c", "user.email=phoenix@example.invalid", "commit", "-q", "-m", f"PhoenixRepo revival {rid}: {result.state}"], target)
    trace.event(agent_name="orchestrator", action="REVIVE_RESULT", input_summary=full_name, output_summary={"state": result.state, "reason": result.reason, "gate1": verdict, "models": sorted(client.served_models), "regressed": (result.quality or {}).get("regressed") or []})
    trace.seal()
    payload = result.as_dict() | {"repo": full_name, "gate1": verdict, "run_id": rid, "run_hash": trace.run_hash, "clone": str(target), "served_models": sorted(client.served_models)}
    write_reports(out, result, full_name, rid)
    (out / "revive.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    emit_result(payload, ("repo", "gate1", "state", "reason", "files_changed", "run_id", "clone"))
    return 0 if result.state in ("RESURRECTED", "NOTHING_TO_FIX") else 1


def emit_result(payload: dict, keys: tuple[str, ...]) -> None:
    """Print a one-screen summary, then the same JSON fields as before."""
    print(revive_loop.format_run_summary(payload))
    print(json.dumps({k: payload[k] for k in keys}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
