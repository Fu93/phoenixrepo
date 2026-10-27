#!/usr/bin/env python3
"""phoenix revive <github-url>: gate 1, then a real build-and-verify loop on a local clone.

Gate 1 runs first (the same judge as `phoenix judge`). The revival loop then runs the repository's
own tests, lets the model patch the code, re-runs the tests and sends real failure output back.
It commits to a local branch only. It never pushes and never opens a pull request.

NEEDS_REVIEW and NEEDS_PRIOR_ART may proceed only with --human-approved, a reviewer, and a note.
ALREADY_EXISTS, NO_GO, and UNJUDGED never proceed, even with those flags.
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
from config.settings import trace_secrets  # noqa: E402
from scripts import judge_repo  # noqa: E402

REUSED_GATE_REASON = "reused from --gate-verdict; not a new gate-1 judgement"
APPROVAL_USAGE = "human approval needs --reviewer and --review-note"


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", nargs="?", help="GitHub repository URL. Omit when --batch is set.")
    ap.add_argument("--workdir", type=Path, default=ROOT / "work")
    ap.add_argument("--iterations", type=int, default=4)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--gate-verdict", default=None, help="Reuse an earlier gate-1 verdict instead of re-judging (CANDIDATE, ALREADY_EXISTS, ...).")
    ap.add_argument("--gate-reason", default="", help="Original gate-1 reason when --gate-verdict is reused. Recorded as reused when omitted.")
    ap.add_argument("--time-budget", type=float, default=2400, dest="time_budget", help="Stop starting new upgrade or refactor items after this many seconds (default 2400).")
    ap.add_argument("--deadline", type=float, default=3600, help="Hard overall deadline in seconds (default 3600). The run ends TIMEOUT rather than hanging.")
    ap.add_argument("--step-timeout", type=int, default=900, dest="step_timeout", help="Cap each command in seconds (default 900).")
    ap.add_argument("--human-approved", action="store_true", help="Proceed after NEEDS_REVIEW or NEEDS_PRIOR_ART. Refused for ALREADY_EXISTS, NO_GO, and UNJUDGED.")
    ap.add_argument("--reviewer", default="", help="Name of the person who approved a NEEDS_REVIEW or NEEDS_PRIOR_ART revival.")
    ap.add_argument("--review-note", default="", dest="review_note", help="Why the reviewer approved this revival.")
    ap.add_argument("--no-plan", action="store_true", dest="no_plan", help="Do not write the rescue plan for a CANDIDATE.")
    ap.add_argument("--batch", type=Path, default=None, help="JSON list of repos. Each item may set url, human_approved, reviewer, review_note, gate_verdict, and gate_reason.")
    return ap


def approval_error(approved: bool, reviewer: str, note: str) -> str | None:
    """Incomplete approval is a usage error. A complete one is checked against the verdict later."""
    if not approved:
        return None
    if not str(reviewer or "").strip() or not str(note or "").strip():
        return APPROVAL_USAGE
    return None


def approval_from_flags(verdict: str, reason: str, approved: bool, reviewer: str, note: str) -> dict[str, str] | None:
    if approval_error(approved, reviewer, note):
        return None
    if not approved:
        return None
    return {
        "reviewer": str(reviewer).strip(),
        "note": str(note).strip(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "reason": reason,
    }


def load_batch(path: Path) -> list[dict]:
    """A JSON list, or an object with a repos or jobs list. Strings are treated as URLs."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        rows = data.get("repos") or data.get("jobs") or []
    elif isinstance(data, list):
        rows = data
    else:
        raise ValueError("batch file must be a list or an object with repos or jobs")
    jobs: list[dict] = []
    for item in rows:
        if isinstance(item, str) and item.strip():
            jobs.append({"url": item.strip()})
        elif isinstance(item, dict) and (item.get("url") or item.get("repo") or item.get("repo_id")):
            job = dict(item)
            job["url"] = str(item.get("url") or item.get("repo") or item.get("repo_id"))
            jobs.append(job)
    return jobs


def _job_value(job: dict, key: str, default):
    return job[key] if key in job else default


def build_relevance_client(**overrides):
    """Meterless client for the revive loop.

    The requested model is Settings.requested_model(): the Nemotron lane unless
    MODEL_NAME (or METERLESS_MODEL) overrides it. This does not add a provider.
    """

    from config.settings import Settings

    settings = Settings()
    params = {
        "base_url": settings.meterless_base_url,
        "token": settings.meterless_runtime_token,
        "model": settings.requested_model(),
    }
    params.update(overrides)
    return RelevanceClient(**params)


def _exit_code(state: str) -> int:
    if state == "NOT_ATTEMPTED_GATE1":
        return 3
    if state in ("RESURRECTED", "NOTHING_TO_FIX"):
        return 0
    return 1


def run_job(args: argparse.Namespace, job: dict, index: int) -> int:
    url = str(job.get("url") or args.url or "").strip()
    if not url:
        sys.stderr.write("batch item is missing a url\n")
        return 2
    approved = bool(_job_value(job, "human_approved", args.human_approved))
    reviewer = str(_job_value(job, "reviewer", args.reviewer) or "")
    note = str(_job_value(job, "review_note", None) or _job_value(job, "note", None) or args.review_note or "")
    usage = approval_error(approved, reviewer, note)
    if usage:
        sys.stderr.write(f"{url}: {usage}\n")
        return 2
    try:
        full_name = judge_repo.parse_repo_url(url)
    except Exception as exc:  # noqa: BLE001 — a bad URL is a usage error, not a revival
        sys.stderr.write(f"{exc}\n")
        return 2

    rid = args.run_id or "revive-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    if index:
        rid = f"{rid}-{index}"
    out = ROOT / "reports" / rid
    out.mkdir(parents=True, exist_ok=True)
    trace = HackathonTrace(rid, ROOT / "logs", trace_secrets())

    supplied = _job_value(job, "gate_verdict", args.gate_verdict)
    supplied_reason = str(_job_value(job, "gate_reason", None) or _job_value(job, "reason", None) or args.gate_reason or "")
    if supplied:
        verdict = str(supplied)
        gate_reason = supplied_reason.strip() or REUSED_GATE_REASON
        trace.event(
            agent_name="market_judge",
            action="GATE1",
            input_summary=full_name,
            output_summary={"verdict": verdict, "reason": gate_reason[:300], "source": "supplied"},
        )
    else:
        gate = judge_repo.judge(url, run_id=rid + "-gate1")
        verdict = gate["verdict"]
        gate_reason = str(gate.get("reason") or "")
        trace.event(
            agent_name="market_judge",
            action="GATE1",
            input_summary=full_name,
            output_summary={"verdict": verdict, "reason": gate_reason[:300], "gate_run": gate["run_id"], "gate_hash": gate.get("run_hash")},
        )

    if verdict == "CANDIDATE" and not supplied and not getattr(args, "no_plan", False):
        _write_rescue_plan(trace, full_name, ROOT / "reports" / f"{rid}-gate1" / "scores.json", out)

    approval = approval_from_flags(verdict, gate_reason, approved, reviewer, note)
    if not revive_loop.revival_allowed(verdict, approval):
        # Do not clone. An approval of ALREADY_EXISTS, NO_GO, or UNJUDGED is recorded and refused.
        result = revive_loop.revive(
            Path("."),
            None,
            trace,
            full_name,
            gate_verdict=verdict,
            gate_reason=gate_reason,
            human_approval=approval,
        )
        return _finish_job(trace, result, full_name, verdict, rid, clone="")

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
    client = build_relevance_client(trace=trace)
    result = revive_loop.revive(
        target,
        client,
        trace,
        full_name,
        description=description,
        gate_verdict=verdict,
        gate_reason=gate_reason,
        human_approval=approval,
        max_iterations=args.iterations,
        time_budget_s=args.time_budget,
        deadline_s=args.deadline,
        step_timeout_s=args.step_timeout,
        commit_each_item=True,
        run_dir=ROOT / "reports" / rid,
    )
    if result.files_changed:
        try:
            _git(["add", "-A"], target)
            _git(["-c", "user.name=PhoenixRepo", "-c", "user.email=phoenix@example.invalid", "commit", "-q", "-m", f"PhoenixRepo revival {rid}: {result.state}"], target)
        except RuntimeError as exc:
            if "nothing to commit" not in str(exc).lower() and "nothing added to commit" not in str(exc).lower():
                raise
    trace.event(
        agent_name="orchestrator",
        action="REVIVE_RESULT",
        input_summary=full_name,
        output_summary={"state": result.state, "reason": result.reason, "gate1": verdict, "models": sorted(client.served_models), "regressed": (result.quality or {}).get("regressed") or [], "terminal": result.terminal, "label": result.label},
    )
    return _finish_job(trace, result, full_name, verdict, rid, clone=str(target), extra={"served_models": sorted(client.served_models)})


def _write_rescue_plan(trace, full_name: str, scores_path: Path, out: Path) -> None:
    """Gate 2 part 1: the rescue plan for a CANDIDATE, in this run's trace. Never stops the revival."""
    from app.rescue_plan import build_plan, render_markdown, repo_tree, to_json

    try:
        data = json.loads(scores_path.read_text(encoding="utf-8"))
        row = next(r for r in data.get("candidates") or [] if str(r.get("repo_id") or "").casefold() == full_name.casefold())
        plan = build_plan(row, client=build_relevance_client(trace=trace), trace=trace, tree=repo_tree(full_name))
        plan["gate1"].update({"run_id": data.get("run_id"), "run_hash": data.get("run_hash")})
        slug = full_name.replace("/", "__")
        for name, text in ((f"{slug}.rescue.md", render_markdown(plan, run_id=trace.run_id)), (f"{slug}.rescue.json", to_json(plan))):
            with open(out / name, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
    except Exception as exc:  # noqa: BLE001
        trace.event(agent_name="rescue_planner", action="RESCUE_PLAN", input_summary=full_name,
                    output_summary={"error": f"{type(exc).__name__}: {str(exc)[:200]}"}, status="error")


def _finish_job(trace, result, full_name: str, verdict: str, rid: str, clone: str, extra: dict | None = None) -> int:
    if not clone:
        trace.event(
            agent_name="orchestrator",
            action="REVIVE_RESULT",
            input_summary=full_name,
            output_summary={"state": result.state, "reason": result.reason, "gate1": verdict, "terminal": result.terminal, "label": result.label},
        )
    trace.seal()
    payload = result.as_dict() | {"repo": full_name, "gate1": verdict, "run_id": rid, "run_hash": trace.run_hash}
    if clone:
        payload["clone"] = clone
    if extra:
        payload.update(extra)
    out = ROOT / "reports" / rid
    write_reports(out, result, full_name, rid)
    (out / "revive.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    keys = ("repo", "gate1", "state", "reason", "run_id", "terminal", "label")
    if clone:
        keys = ("repo", "gate1", "state", "reason", "files_changed", "run_id", "clone", "terminal", "label")
    emit_result(payload, keys)
    return _exit_code(result.state)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.batch:
        try:
            jobs = load_batch(args.batch)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            sys.stderr.write(f"could not read batch file: {exc}\n")
            return 2
        if not jobs:
            sys.stderr.write("batch file has no repos\n")
            return 2
        return max(run_job(args, job, index) for index, job in enumerate(jobs, start=1))
    if not args.url:
        sys.stderr.write("pass a repository URL or --batch\n")
        return 2
    usage = approval_error(args.human_approved, args.reviewer, args.review_note)
    if usage:
        sys.stderr.write(usage + "\n")
        return 2
    return run_job(args, {"url": args.url}, 0)


def emit_result(payload: dict, keys: tuple[str, ...]) -> None:
    """Print a one-screen summary, then the same JSON fields as before."""
    print(revive_loop.format_run_summary(payload))
    print(json.dumps({k: payload[k] for k in keys if k in payload}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    from app.console import prepare_cli

    prepare_cli()
    raise SystemExit(main())
