#!/usr/bin/env python3
"""Run score_candidates.py on N disjoint shards of a discovery snapshot in parallel.

Each shard is a full copy of the snapshot whose discovery_candidates.csv holds only that
shard's rows, so every shard keeps its own trace (logs/trace-<run>-sK.jsonl) and hash
chain. Successful LLM answers and tool results are cached on disk, so re-running the
same command resumes: finished work becomes cache hits. Outputs are merged into
logs/<run>_scores.json and logs/<run>_scores.md. The protagonist is NOT auto-selected
across shards; it is chosen with a human from the merged ranking.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_shards(snapshot: Path, out_dir: Path, n: int) -> list[Path]:
    with (snapshot / "discovery_candidates.csv").open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        rows = list(reader)
    shards = []
    for k in range(n):
        d = out_dir / f"shard{k}"
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(snapshot, d)
        with (d / "discovery_candidates.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            w.writerows(rows[k::n])
        shards.append(d)
    return shards


def merge(run_id: str, parts: list[dict], log_dir: Path) -> dict:
    model_calls: dict[str, int] = {}
    for p in parts:
        for m, c in (p.get("model_calls") or {}).items():
            model_calls[m] = model_calls.get(m, 0) + int(c)
    merged = {
        "run_id": run_id,
        "shard_run_ids": [p.get("run_id") for p in parts],
        "shard_run_hashes": [p.get("run_hash") for p in parts],
        "llm_used": all(p.get("llm_used") for p in parts),
        "model_calls": model_calls,
        "warnings": [w for p in parts for w in (p.get("warnings") or [])],
        "candidates": [c for p in parts for c in (p.get("candidates") or [])],
        "ineligible": [r for p in parts for r in (p.get("ineligible") or [])],
        "protagonist": None,
        "protagonist_note": "Not auto-selected across shards; choose with a human from the merged ranking.",
    }
    return merged


def render_md(m: dict) -> str:
    def total(c):
        for k in ("weighted_total", "total", "score"):
            if isinstance(c.get(k), (int, float)):
                return c[k]
        return 0
    lines = [f"# Merged scorecard `{m['run_id']}`", "", f"Shards: {', '.join(map(str, m['shard_run_ids']))}", ""]
    lines += ["## Model calls", "", "| Served model | Calls |", "| :--- | :---: |"]
    lines += [f"| `{k}` | {v} |" for k, v in sorted(m["model_calls"].items())]
    lines += ["", f"## Eligible candidates ({len(m['candidates'])})", "", "| Repository | Total |", "| :--- | :---: |"]
    for c in sorted(m["candidates"], key=total, reverse=True):
        lines.append(f"| `{c.get('repo_id')}` | {total(c)} |")
    counts: dict[str, int] = {}
    for r in m["ineligible"]:
        counts[str(r.get("verdict"))] = counts.get(str(r.get("verdict")), 0) + 1
    lines += ["", f"## Ineligible ({len(m['ineligible'])})", "", ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())), "",
              "| Repository | Verdict | Reason |", "| :--- | :---: | :--- |"]
    for r in m["ineligible"]:
        reason = str(r.get("reason") or "").replace("|", "/").replace("\n", " ")
        lines.append(f"| `{r.get('repo_id')}` | `{r.get('verdict')}` | {reason[:300]} |")
    from scripts.evidence_report import format_decision_lines

    lines += ["", "## Why a repository was refused or sent to review", ""]
    shown = False
    for r in m["ineligible"]:
        block = format_decision_lines(
            str(r.get("repo_id") or ""),
            str(r.get("verdict") or ""),
            str(r.get("reason") or ""),
            r.get("decision_evidence"),
        )
        if block:
            shown = True
            lines.extend(block)
    if not shown:
        lines.append("No ALREADY_EXISTS or NEEDS_REVIEW row was recorded.")
        lines.append("")
    lines += ["", "## Rescue potential", "", "This score does not change the verdict.", ""]
    lines += ["| Repository | Verdict | Score | Stars | Stale days | CI |", "| :--- | :---: | :---: | :---: | :---: | :--- |"]
    ranked = list(m.get("candidates") or []) + list(m.get("ineligible") or [])
    ranked.sort(key=lambda row: -float(((row.get("rescue_potential") or {}).get("score") or 0)))
    for row in ranked:
        rescue = row.get("rescue_potential") or {}
        comp = rescue.get("components") or {}
        lines.append(
            f"| `{row.get('repo_id')}` | `{row.get('verdict')}` | {rescue.get('score', '')} | {comp.get('stars', '')} | {comp.get('staleness_days', '')} | {comp.get('ci_state', '')} |"
        )
    lines += ["", m["protagonist_note"], ""]
    return "\n".join(lines)


def count_unjudged(merged: dict) -> int:
    return sum(1 for r in merged.get("ineligible") or [] if r.get("verdict") == "UNJUDGED")


def run_round(args, shards: list[Path], suffix: str) -> tuple[list[dict], list[tuple[str, int]], list[str]]:
    args.log_dir.mkdir(parents=True, exist_ok=True)
    procs = []
    for k, d in enumerate(shards):
        rid = f"{args.run_id}{suffix}-s{k}"
        # A scores file left by an earlier run with the same run id would otherwise be merged
        # even when this shard crashes before writing its own.
        (args.log_dir / f"{rid}_scores.json").unlink(missing_ok=True)
        (args.log_dir / f"{rid}_scores.md").unlink(missing_ok=True)
        cmd = [sys.executable, str(ROOT / "scripts" / "score_candidates.py"), "--snapshot", str(d), "--llm",
               "--max-calls", str(args.max_calls), "--max-seconds", str(args.max_seconds), "--run-id", rid,
               "--log-dir", str(args.log_dir), "--output-json", str(args.log_dir / f"{rid}_scores.json"),
               "--output-md", str(args.log_dir / f"{rid}_scores.md")]
        out = open(args.log_dir / f"{rid}.out", "w", encoding="utf-8")  # noqa: SIM115 — closed after wait()
        err = open(args.log_dir / f"{rid}.err", "w", encoding="utf-8")  # noqa: SIM115
        try:
            # UTF-8 in the shard's stdout/stderr files whatever the Windows code page is.
            child_env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=out, stderr=err, env=child_env)
        except OSError:
            out.close()
            err.close()
            raise
        procs.append((rid, proc, out, err))
        time.sleep(5)
    failed = []
    for rid, p, out, err in procs:
        rc = p.wait()
        out.close()
        err.close()
        if rc != 0:
            failed.append((rid, rc))
    parts = []
    for rid, *_ in procs:
        f = args.log_dir / f"{rid}_scores.json"
        if f.exists():
            try:
                parts.append(json.loads(f.read_text(encoding="utf-8")))
            except ValueError:
                failed.append((rid, -1))
    return parts, failed, [rid for rid, *_ in procs]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=Path, required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--shards", type=int, default=2, help="Parallel shards. Fewer shards mean fewer Nightshift 502s.")
    ap.add_argument("--max-rounds", type=int, default=3, help="Rerun rounds while repos are still UNJUDGED (cached work is skipped).")
    ap.add_argument("--round-wait", type=float, default=60.0, help="Seconds to wait before a rerun round.")
    ap.add_argument("--log-dir", type=Path, default=ROOT / "logs")
    ap.add_argument("--max-calls", type=int, default=3000)
    ap.add_argument("--max-seconds", type=float, default=21600)
    args = ap.parse_args()

    shard_root = ROOT / "data" / "discovery" / "_shards" / args.run_id
    shard_root.mkdir(parents=True, exist_ok=True)
    shards = make_shards(args.snapshot, shard_root, args.shards)
    merged: dict = {}
    failed: list[tuple[str, int]] = []
    rounds_run = 0
    for rnd in range(1, max(1, args.max_rounds) + 1):
        rounds_run = rnd
        suffix = "" if rnd == 1 else f"-r{rnd}"
        parts, failed, _ = run_round(args, shards, suffix)
        merged = merge(args.run_id, parts, args.log_dir)
        merged["failed_shards"] = [{"run_id": r, "exit_code": c} for r, c in failed]
        merged["rounds"] = rnd
        merged["unjudged"] = count_unjudged(merged)
        if merged["unjudged"] == 0 and not failed:
            break
        if rnd < args.max_rounds:
            sys.stderr.write(f"round {rnd}: {merged['unjudged']} UNJUDGED, {len(failed)} failed shards; rerunning\n")
            time.sleep(args.round_wait)
    (args.log_dir / f"{args.run_id}_scores.json").write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.log_dir / f"{args.run_id}_scores.md").write_text(render_md(merged), encoding="utf-8")
    print(json.dumps({"shards": len(shards), "rounds": rounds_run, "failed": merged["failed_shards"],
                      "candidates": len(merged["candidates"]), "ineligible": len(merged["ineligible"]),
                      "unjudged": merged["unjudged"]}))
    return 1 if failed else 0


if __name__ == "__main__":
    from app.console import prepare_cli

    prepare_cli()
    raise SystemExit(main())
