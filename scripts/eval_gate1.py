#!/usr/bin/env python3
"""Check gate-1 labels, and optionally compare them with a live Nightshift judgement.

``data/eval/gate1_labels.json`` holds a few repositories whose expected verdict is a human
label. Each label cites a quote, and that quote is a substring of text stored in the same
file (copied from the repository README or from an alternative's own text). This script
checks those quotes. It does not invent a verdict.

When ``METERLESS_RUNTIME_TOKEN`` is set to a real token, it runs ``scripts/judge_repo.py``
for each case and writes how many live verdicts sit in the label's acceptable set. The
placeholder ``rt-local`` does not count. With no token it only validates the label file and
writes ``live_ran: false``. Agreement is null in that case. Tests must not call the live
judge; they pass no token, or they pass a fake runner that does not talk to Nightshift.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LABELS = ROOT / "data" / "eval" / "gate1_labels.json"
VERDICTS = {"CANDIDATE", "ALREADY_EXISTS", "NO_GO", "NEEDS_REVIEW", "UNJUDGED", "NEEDS_PRIOR_ART"}

# Human labels this file is required to keep. The quotes in the file are what justify them.
REQUIRED = {
    "orangekame3/mirrormate": {"CANDIDATE", "NEEDS_REVIEW"},
    "Fu93/hygen": {"ALREADY_EXISTS"},
    "SergioMendolia/ytkb": {"ALREADY_EXISTS"},
    "tharani-2006/Invoice-generator-application": {"ALREADY_EXISTS"},
}


def token_present() -> bool:
    token = os.environ.get("METERLESS_RUNTIME_TOKEN") or ""
    return bool(token) and token != "rt-local"


def validate_labels(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        return ["label file has no cases"]
    seen: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict):
            errors.append("a case is not an object")
            continue
        repo_id = str(case.get("repo_id") or "")
        if not repo_id:
            errors.append("a case has no repo_id")
            continue
        seen[repo_id] = case
        acceptable = case.get("acceptable") or []
        if not isinstance(acceptable, list) or not acceptable:
            errors.append(f"{repo_id}: acceptable verdicts are missing")
            acceptable = []
        for verdict in acceptable:
            if verdict not in VERDICTS:
                errors.append(f"{repo_id}: unknown verdict {verdict}")
        forbidden = case.get("forbidden") or []
        if not isinstance(forbidden, list):
            errors.append(f"{repo_id}: forbidden must be a list")
            forbidden = []
        overlap = set(acceptable) & set(forbidden)
        if overlap:
            errors.append(f"{repo_id}: acceptable and forbidden both contain {sorted(overlap)}")
        evidence = case.get("evidence") or []
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{repo_id}: no quoted evidence")
            evidence = []
        roles = set()
        for item in evidence:
            if not isinstance(item, dict):
                errors.append(f"{repo_id}: evidence item is not an object")
                continue
            quote = str(item.get("quote") or "")
            text = str(item.get("text") or "")
            roles.add(str(item.get("role") or ""))
            if not quote or quote not in text:
                errors.append(f"{repo_id}: quote is not a substring of the stored text: {quote[:80]!r}")
            if not item.get("source_url"):
                errors.append(f"{repo_id}: evidence is missing source_url")
        if acceptable == ["ALREADY_EXISTS"] and not ({"candidate", "alternative"} <= roles):
            errors.append(f"{repo_id}: an ALREADY_EXISTS label needs a candidate quote and an alternative quote")
        if "ALREADY_EXISTS" in forbidden and "candidate" not in roles:
            errors.append(f"{repo_id}: a label that forbids ALREADY_EXISTS needs the candidate's own quote")
    for repo_id, expected in REQUIRED.items():
        case = seen.get(repo_id)
        if case is None:
            errors.append(f"missing required case {repo_id}")
            continue
        got = set(case.get("acceptable") or [])
        if got != expected:
            errors.append(f"{repo_id}: acceptable {sorted(got)} does not match the required label {sorted(expected)}")
        if repo_id == "orangekame3/mirrormate" and "ALREADY_EXISTS" in got:
            errors.append("orangekame3/mirrormate must not be labelled ALREADY_EXISTS")
    return errors


def live_judge(repo_id: str) -> dict[str, Any]:
    """The real gate-1 command. Not used by tests."""
    from scripts.judge_repo import judge

    return judge(repo_id)


def evaluate(
    path: Path = LABELS,
    runner: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_labels(payload)
    report: dict[str, Any] = {
        "labels_valid": not errors,
        "label_errors": errors,
        "live_ran": False,
        "agreement": None,
        "results": [],
        "note": "",
    }
    if errors:
        report["note"] = "Label file failed validation. The live judge was not run."
        return report
    if not token_present():
        report["note"] = (
            "No METERLESS_RUNTIME_TOKEN. The label file was validated. "
            "The live judge was not run. No agreement number is claimed."
        )
        return report
    judge = runner or live_judge
    compared = 0
    agreed = 0
    not_compared = 0
    for case in payload["cases"]:
        repo_id = case["repo_id"]
        try:
            result = judge(repo_id)
        except Exception as exc:  # noqa: BLE001 — record the failure, do not invent a verdict
            report["results"].append({
                "repo_id": repo_id,
                "acceptable": case["acceptable"],
                "verdict": None,
                "agreed": False,
                "error": str(exc),
            })
            not_compared += 1
            continue
        verdict = result.get("verdict") if isinstance(result, dict) else None
        if not verdict:
            report["results"].append({
                "repo_id": repo_id,
                "acceptable": case["acceptable"],
                "verdict": None,
                "agreed": False,
                "error": "the judge returned no verdict",
            })
            not_compared += 1
            continue
        ok = verdict in case["acceptable"]
        compared += 1
        agreed += int(ok)
        report["results"].append({
            "repo_id": repo_id,
            "acceptable": case["acceptable"],
            "verdict": verdict,
            "agreed": ok,
            "reason": result.get("reason") if isinstance(result, dict) else "",
        })
    report["live_ran"] = True
    report["agreement"] = {"compared": compared, "agreed": agreed, "not_compared": not_compared}
    report["note"] = "Live judgements are Nightshift verdicts for this run. They are not a stored accuracy."
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, default=LABELS)
    parser.add_argument("--out", type=Path, default=None, help="Write the report JSON here. Also printed.")
    args = parser.parse_args()
    report = evaluate(args.labels)
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if report["labels_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
