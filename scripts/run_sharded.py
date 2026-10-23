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
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
    lines += ["", m["protagonist_note"], ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=Path, required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--shards", type=int, default=4)
    ap.add_argument("--log-dir", type=Path, default=ROOT / "logs")
    ap.add_argument("--max-calls", type=int, default=3000)
    ap.add_argument("--max-seconds", type=float, default=21600)
    args = ap.parse_args()

    shard_root = ROOT / "data" / "discovery" / "_shards" / args.run_id
    shard_root.mkdir(parents=True, exist_ok=True)
    shards = make_shards(args.snapshot, shard_root, args.shards)
    procs = []
    for k, d in enumerate(shards):
        rid = f"{args.run_id}-s{k}"
        cmd = [sys.executable, str(ROOT / "scripts" / "score_candidates.py"), "--snapshot", str(d), "--llm",
               "--max-calls", str(args.max_calls), "--max-seconds", str(args.max_seconds), "--run-id", rid,
               "--log-dir", str(args.log_dir), "--output-json", str(args.log_dir / f"{rid}_scores.json"),
               "--output-md", str(args.log_dir / f"{rid}_scores.md")]
        out = open(args.log_dir / f"{rid}.out", "w", encoding="utf-8")
        err = open(args.log_dir / f"{rid}.err", "w", encoding="utf-8")
        procs.append((rid, subprocess.Popen(cmd, cwd=ROOT, stdout=out, stderr=err), out, err))
        time.sleep(5)
    failed = []
    for rid, p, out, err in procs:
        rc = p.wait()
        out.close(); err.close()
        if rc != 0:
            failed.append((rid, rc))
    parts = []
    for rid, *_ in procs:
        f = args.log_dir / f"{rid}_scores.json"
        if f.exists():
            parts.append(json.loads(f.read_text(encoding="utf-8")))
    merged = merge(args.run_id, parts, args.log_dir)
    merged["failed_shards"] = [{"run_id": r, "exit_code": c} for r, c in failed]
    (args.log_dir / f"{args.run_id}_scores.json").write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    (args.log_dir / f"{args.run_id}_scores.md").write_text(render_md(merged), encoding="utf-8")
    print(json.dumps({"shards": len(procs), "failed": merged["failed_shards"], "candidates": len(merged["candidates"]),
                      "ineligible": len(merged["ineligible"])}))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
