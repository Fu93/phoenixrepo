#!/usr/bin/env python3
"""Write the scored numbers into the judge-facing docs from one eval report and its traces.

    python scripts/fill_results.py logs/eval-nemotron-v3.json            # print what would be written
    python scripts/fill_results.py logs/eval-nemotron-v3.json --write    # write README.md and the draft

Every number comes from the report written by scripts/eval_gate1.py and from the trace file
of each judged case (logs/trace-<run_id>.jsonl), checked with scripts/verify_trace.py. A slot
is the text between ``<!-- BEGIN numbers: NAME -->`` and ``<!-- END numbers: NAME -->``; the
markers stay, so the command can be run again on the final commit. A number the files do not
hold is written as TBD, never estimated. The Zetaris slot is not touched: it comes from a
different command.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval_gate1 import LABELS  # noqa: E402
from scripts.eval_summary import markdown, summarise  # noqa: E402
from scripts.rerun_unjudged import is_infra_review  # noqa: E402
from scripts.verify_trace import nemotron_report, verify  # noqa: E402

PROTAGONIST = "DSRegoitia/CalendarSyncProject"
SKELETON = "62a1540"
TARGETS = (ROOT / "README.md", ROOT / "docs" / "SUBMISSION_DRAFT.md")


def _cell(text: Any, limit: int = 110) -> str:
    one = " ".join(str(text or "").split()).replace("|", "\\|")
    return one if len(one) <= limit else one[: limit - 1].rstrip() + "…"


def _cache_hits(path: Path) -> int:
    hits = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "served_model" in record and str(record.get("status") or "") not in {"error", "rejected"}:
            if '"cache_hit": true' in str(record.get("output_summary") or ""):
                hits += 1
    return hits


def _verdict_answer(path: Path) -> dict[str, str] | None:
    """The used answer of the last discovery (final-verdict) call in the trace."""
    found = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "served_model" not in record or str(record.get("status") or "") in {"error", "rejected"}:
            continue
        inp = record.get("input_summary")
        if isinstance(inp, str):
            try:
                inp = json.loads(inp)
            except json.JSONDecodeError:
                inp = {"step": "discovery"} if '"step": "discovery"' in inp else {}
        if isinstance(inp, dict) and inp.get("step") == "discovery":
            out = str(record.get("output_summary") or "")
            found = {"served_model": str(record.get("served_model") or ""),
                     "provider": "nvidia-direct" if '"provider": "nvidia-direct"' in out else "nightshift"}
    return found


def trace_facts(run_id: str | None, logs: Path) -> dict[str, Any]:
    if not run_id:
        return {"found": False}
    path = logs / f"trace-{run_id}.jsonl"
    if not path.exists():
        return {"found": False, "path": path}
    ok, detail = verify(path)
    facts: dict[str, Any] = {"found": True, "path": path, "chain_ok": ok}
    if ok:
        facts["run_hash"] = detail
        facts.update(nemotron_report(path))
        facts["cache_hits"] = _cache_hits(path)
        facts["verdict_answer"] = _verdict_answer(path)
    else:
        facts["chain_error"] = detail
    return facts


def collect(report: dict[str, Any], labels: dict[str, Any], logs: Path) -> dict[str, Any]:
    cases = {case["repo_id"]: case for case in labels.get("cases") or []}
    rows = [r for r in report.get("results") or [] if isinstance(r, dict) and r.get("repo_id") in cases]
    per_case = []
    totals = {"traces": 0, "chain_ok": 0, "answers_used": 0, "answers_by_nemotron": 0, "cache_hits": 0,
              "providers": {}, "not_nemotron": [], "verdicts": 0, "verdicts_by_nemotron": 0}
    for row in rows:
        facts = trace_facts(row.get("run_id"), logs)
        if facts.get("found"):
            totals["traces"] += 1
        if facts.get("chain_ok"):
            totals["chain_ok"] += 1
            totals["answers_used"] += facts["answers_used"]
            totals["answers_by_nemotron"] += facts["answers_by_nemotron"]
            totals["cache_hits"] += facts["cache_hits"]
            for name, count in facts["providers"].items():
                totals["providers"][name] = totals["providers"].get(name, 0) + count
            totals["not_nemotron"] += [f"{row['repo_id']} {item}" for item in facts["not_nemotron"]]
            answer = facts.get("verdict_answer")
            if answer:
                totals["verdicts"] += 1
                totals["verdicts_by_nemotron"] += int(answer["served_model"].startswith("nvidia/nemotron-"))
        infra = not row.get("verdict") or is_infra_review({"verdict": row.get("verdict"), "reason": row.get("reason") or ""})
        acceptable = cases[row["repo_id"]].get("acceptable") or []
        per_case.append({
            "repo_id": row["repo_id"],
            "acceptable": acceptable,
            "verdict": row.get("verdict") or "",
            "outcome": "not judged" if infra else ("agrees" if row.get("verdict") in acceptable else "disagrees"),
            "reason": row.get("reason") or row.get("error") or "",
            "run_id": row.get("run_id") or "",
            "trace": facts,
        })
    return {
        "summary": summarise(report, labels),
        "per_case": per_case,
        "totals": totals,
        "rows": len(rows),
        "protagonist": next((c for c in per_case if c["repo_id"] == PROTAGONIST), None),
    }


def _commit_count() -> str:
    try:
        out = subprocess.run(["git", "rev-list", "--count", f"{SKELETON}..HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True, timeout=30)
        return out.stdout.strip() or "TBD"
    except (OSError, subprocess.SubprocessError):
        return "TBD"


def per_case_table(data: dict[str, Any]) -> str:
    lines = [
        "| Repository | Human label | Verdict | Outcome | Verdict answer by | Nemotron answers used | Providers | Chain | Reason (shortened) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for case in data["per_case"]:
        t = case["trace"]
        answer = t.get("verdict_answer") if t.get("chain_ok") else None
        by = f"`{answer['served_model']}` ({answer['provider']})" if answer else "none"
        if t.get("chain_ok"):
            nemo = f"{t['answers_by_nemotron']} of {t['answers_used']}"
            prov = ", ".join(f"{k} {v}" for k, v in t["providers"].items()) or "none"
            chain = f"ok `{t['run_hash'][:12]}`"
        elif t.get("found"):
            nemo, prov, chain = "TBD", "TBD", "FAILED"
        else:
            nemo, prov, chain = "TBD", "TBD", "trace not found"
        lines.append(
            f"| `{case['repo_id']}` | {' or '.join(case['acceptable'])} | {case['verdict'] or 'none'} | {case['outcome']} "
            f"| {by} | {nemo} | {prov} | {chain} | {_cell(case['reason'])} |"
        )
    lines.append("")
    lines.append("Each row's trace is `logs/trace-<run_id>.jsonl` on the author's machine; the chain column is `scripts/verify_trace.py` on that file.")
    return "\n".join(lines)


def slots(data: dict[str, Any], source: str, readme: bool) -> dict[str, str]:
    t = data["totals"]
    out: dict[str, str] = {}
    out["eval-summary-table"] = markdown(data["summary"], source).rstrip()
    if readme:
        out["per-case-table"] = per_case_table(data)
    traced = f"{t['chain_ok']} of {data['rows']} case traces verified"
    if t["chain_ok"]:
        share = f"**{t['answers_by_nemotron']}** of **{t['answers_used']}** answers used ({traced}, `{source}`)."
        if t["verdicts"]:
            share += (f" The final-verdict answer (the `discovery` step) was written by Nemotron in **{t['verdicts_by_nemotron']}** "
                      f"of the **{t['verdicts']}** cases whose trace has one.")
        if t["not_nemotron"]:
            share += " Not Nemotron: " + "; ".join(t["not_nemotron"]) + "."
        split = ", ".join(f"{name} **{count}**" for name, count in sorted(t["providers"].items()))
        cached = (
            f" {t['cache_hits']} of the used answers were replayed from the LLM cache: an earlier run asked the same "
            "prompt with the same input, and the cached line keeps the model and provider that answered then."
            if t["cache_hits"] else ""
        )
        out["provider-split"] = (
            f"Provider split on that run, from `verify_trace.py --require-nemotron` over each case trace: {split}.{cached} "
            "Every `nvidia-direct` call was served by `nvidia/nemotron-3-ultra-550b-a55b`, because NVIDIA's API answered 404 "
            "for the 120B Super model on this account."
        )
    else:
        share = f"**TBD** ({traced})."
        out["provider-split"] = "Provider split on that run: **TBD** (no verified case trace)."
    out["nemotron-answer-share"] = "Answers by NVIDIA Nemotron on the scored run: " + share
    hero = data["protagonist"]
    if hero and hero["verdict"]:
        out["calendarsync-final-verdict"] = (
            f"Final verdict on the recorded run (`{hero['run_id']}`): **{hero['verdict']}** ({hero['outcome']} with the human label). "
            f"Reason: {_cell(hero['reason'], 300)}"
        )
        if hero["trace"].get("chain_ok"):
            h = hero["trace"]["run_hash"]
            out["calendarsync-final-run-hash"] = (
                f"Final run hash: `{h}`. Check it with "
                f"`python scripts/verify_trace.py logs/trace-{hero['run_id']}.jsonl --expected-run-hash {h} --require-nemotron`."
            )
        else:
            out["calendarsync-final-run-hash"] = "Final run hash: **TBD** (trace not found or not verified)."
    if not readme:
        out["build-window-commit-count"] = (
            f"Commit count after the skeleton, on the commit these numbers were written from: **{_commit_count()}** "
            f"(`git rev-list --count {SKELETON}..HEAD`). Run `scripts/fill_results.py` again on the final commit."
        )
    return out


def apply(text: str, values: dict[str, str]) -> tuple[str, list[str]]:
    done = []
    for name, body in values.items():
        pattern = re.compile(
            r"(<!-- BEGIN numbers: " + re.escape(name) + r" -->\n)(.*?)(\n<!-- END numbers: " + re.escape(name) + r" -->)", re.S
        )
        text, n = pattern.subn(lambda m: m.group(1) + body + m.group(3), text)
        if n:
            done.append(name)
    return text, done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", type=Path)
    ap.add_argument("--labels", type=Path, default=LABELS)
    ap.add_argument("--logs", type=Path, default=ROOT / "logs")
    ap.add_argument("--write", action="store_true", help="Write README.md and docs/SUBMISSION_DRAFT.md.")
    args = ap.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    data = collect(report, labels, args.logs)
    try:
        source = args.report.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        source = args.report.as_posix()
    for target in TARGETS:
        values = slots(data, source, readme=target.name == "README.md")
        if args.write:
            text, done = apply(target.read_text(encoding="utf-8"), values)
            target.write_text(text, encoding="utf-8")
            print(f"{target.relative_to(ROOT)}: wrote {', '.join(done) or 'nothing'}")
        else:
            print(f"===== {target.relative_to(ROOT)}")
            for name, body in values.items():
                print(f"--- {name}\n{body}\n")
    t = data["totals"]
    if t["not_nemotron"]:
        print("warning: some used answers were not Nemotron: " + "; ".join(t["not_nemotron"]), file=sys.stderr)
    if t["chain_ok"] < data["rows"]:
        print(f"warning: only {t['chain_ok']} of {data['rows']} case traces verified (--logs {args.logs})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    from app.console import prepare_cli

    prepare_cli()
    raise SystemExit(main())
