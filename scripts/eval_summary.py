#!/usr/bin/env python3
"""Turn an eval_gate1.py report into the numbers and the Markdown the README quotes.

    python scripts/eval_summary.py logs/eval-nemotron.json
    python scripts/eval_summary.py logs/eval-nemotron.json --markdown > docs/RESULTS.md

Everything is recomputed from the report's ``results`` rows and the label file, so a report
written by an older eval_gate1.py (which counted UNJUDGED as a disagreement) is summarised the
same way: UNJUDGED and an infrastructure NEEDS_REVIEW are "not judged", never a disagreement,
never a false pass. Nothing is estimated; a number the report does not hold is not printed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.eval_gate1 import LABELS  # noqa: E402
from scripts.rerun_unjudged import is_infra_review  # noqa: E402


def summarise(report: dict[str, Any], labels: dict[str, Any]) -> dict[str, Any]:
    cases = {case["repo_id"]: case for case in labels.get("cases") or []}
    rows = [row for row in report.get("results") or [] if isinstance(row, dict) and row.get("repo_id") in cases]
    judged: list[dict[str, Any]] = []
    not_judged: list[dict[str, Any]] = []
    for row in rows:
        verdict = row.get("verdict")
        if not verdict or is_infra_review({"verdict": verdict, "reason": row.get("reason") or row.get("error") or ""}):
            not_judged.append(row)
        else:
            judged.append(row)
    agreed = [row for row in judged if row["verdict"] in (cases[row["repo_id"]].get("acceptable") or [])]
    over_refusal = [
        row["repo_id"] for row in judged
        if cases[row["repo_id"]].get("audit")
        and row["verdict"] == "ALREADY_EXISTS"
        and "ALREADY_EXISTS" not in (cases[row["repo_id"]].get("acceptable") or [])
    ]
    false_pass = [
        row["repo_id"] for row in judged
        if cases[row["repo_id"]].get("audit")
        and set(cases[row["repo_id"]].get("acceptable") or []) == {"ALREADY_EXISTS"}
        and row["verdict"] != "ALREADY_EXISTS"
    ]
    audit_judged = [row for row in judged if cases[row["repo_id"]].get("audit")]
    used = sum(int(row.get("answers_used") or 0) for row in rows)
    by_nemotron = sum(int(row.get("answers_by_nemotron") or 0) for row in rows)
    served: dict[str, int] = {}
    for row in rows:
        for name, calls in (row.get("served_models") or {}).items():
            served[name] = served.get(name, 0) + int(calls or 0)
    should_refuse = [r for r in audit_judged if set(cases[r["repo_id"]].get("acceptable") or []) == {"ALREADY_EXISTS"}]
    should_not = [r for r in audit_judged if "ALREADY_EXISTS" not in (cases[r["repo_id"]].get("acceptable") or [])]
    return {
        "cases_in_report": len(rows),
        "judged": len(judged),
        "not_judged": [row["repo_id"] for row in not_judged],
        "agreed": len(agreed),
        "disagreed": [row["repo_id"] for row in judged if row not in agreed],
        "audit_judged": len(audit_judged),
        "should_refuse_judged": len(should_refuse),
        "should_not_refuse_judged": len(should_not),
        "over_refusal": over_refusal,
        "false_pass": false_pass,
        "answers_used": used,
        "answers_by_nemotron": by_nemotron,
        "served_models": dict(sorted(served.items())),
        "candidates": [row["repo_id"] for row in judged if row["verdict"] == "CANDIDATE"],
        "not_started": list(report.get("not_started") or []),
    }


def markdown(summary: dict[str, Any], source: str) -> str:
    s = summary
    share = f"{s['answers_by_nemotron']} of {s['answers_used']}" if s["answers_used"] else "no model answers recorded"
    lines = [
        "| Measure | Result |",
        "| --- | --- |",
        f"| Labelled repositories judged | {s['judged']} of {s['cases_in_report']} in the report"
        + (f" ({len(s['not_judged'])} not judged: Nightshift or source failure, not counted)" if s["not_judged"] else "")
        + " |",
        f"| Verdict inside the human label's acceptable set | {s['agreed']} of {s['judged']} |",
        f"| Over-refusals (ALREADY_EXISTS where the label says it should not be) | {len(s['over_refusal'])} of {s['should_not_refuse_judged']} |",
        f"| False passes (not refused where the label says ALREADY_EXISTS) | {len(s['false_pass'])} of {s['should_refuse_judged']} |",
        f"| Model answers used that NVIDIA Nemotron wrote | {share} |",
    ]
    if s["served_models"]:
        lines.append("| Served models | " + ", ".join(f"`{name}` {calls}" for name, calls in s["served_models"].items()) + " |")
    lines += ["", f"Source: `{source}`, summarised by `scripts/eval_summary.py`. A live run on one machine, not a stored accuracy."]
    if s["not_started"]:
        lines.append(f"Not started before the deadline: {', '.join(s['not_started'])}.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("report", type=Path)
    ap.add_argument("--labels", type=Path, default=LABELS)
    ap.add_argument("--markdown", action="store_true", help="Print the README table instead of JSON.")
    args = ap.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    labels = json.loads(args.labels.read_text(encoding="utf-8"))
    summary = summarise(report, labels)
    if args.markdown:
        sys.stdout.write(markdown(summary, args.report.as_posix()))
    else:
        sys.stdout.write(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    from app.console import prepare_cli

    prepare_cli()
    raise SystemExit(main())
