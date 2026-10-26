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
    "goodreasonai/ScrapeServ": {"ALREADY_EXISTS"},
    "Selfdb-io/SelfDB": {"ALREADY_EXISTS"},
    "rianvdm/raindrop-wordpress-linkblog-sync": {"ALREADY_EXISTS"},
    "Shabi-x/go-file-sync": {"ALREADY_EXISTS"},
    "DSRegoitia/CalendarSyncProject": {"CANDIDATE", "NEEDS_REVIEW"},
    "samuelarogbonlo/knetvis": {"CANDIDATE", "NEEDS_REVIEW"},
    "Carvenjizaks/gc-sync-webhook": {"CANDIDATE", "NEEDS_REVIEW"},
    "aa12gq/stack-file-sync-vscode": {"CANDIDATE", "NEEDS_REVIEW"},
    "ri-char/pwatch": {"CANDIDATE", "NEEDS_REVIEW"},
    "DayanK/LinkedinSyncContacts": {"CANDIDATE", "NEEDS_REVIEW"},
    "samwho/spacer": {"CANDIDATE", "NEEDS_REVIEW"},
    "oleksiiluchnikov/obsidian-localtable": {"CANDIDATE", "NEEDS_REVIEW"},
    "sealdice/qq-group-files-sync": {"CANDIDATE", "NEEDS_REVIEW"},
    "Dhaiwat10/x402-sovereign": {"CANDIDATE", "NEEDS_REVIEW"},
    "0xMH/fichero-printer": {"CANDIDATE", "NEEDS_REVIEW"},
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
        audit = case.get("audit")
        if isinstance(audit, dict):
            if audit.get("class") not in {"LEGIT", "WEAK", "WRONG"}:
                errors.append(f"{repo_id}: audit class must be LEGIT, WEAK, or WRONG")
            if audit.get("date") != "2026-10-25":
                errors.append(f"{repo_id}: audit date must be 2026-10-25")
            if "phoenix_overrefusal_audit" not in str(audit.get("source") or ""):
                errors.append(f"{repo_id}: audit source must name the over-refusal audit")
            if not isinstance(audit.get("blocker"), dict) or not isinstance(audit.get("candidate"), dict):
                errors.append(f"{repo_id}: audit needs candidate and blocker facts")
            if audit.get("class") == "LEGIT" and set(case.get("acceptable") or []) != {"ALREADY_EXISTS"}:
                errors.append(f"{repo_id}: a LEGIT audit label has to be ALREADY_EXISTS")
            if audit.get("class") in {"WEAK", "WRONG"} and "ALREADY_EXISTS" in set(case.get("acceptable") or []):
                errors.append(f"{repo_id}: a WEAK or WRONG audit label must not accept ALREADY_EXISTS")
    return errors


def rule_observation(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply the maturity rule to the audit fixtures. This is not a live Nightshift score."""
    from datetime import date

    from app.agents.blocker import evaluate_blocker

    over: list[str] = []
    false: list[str] = []
    compared = 0
    for case in payload.get("cases") or []:
        audit = case.get("audit") if isinstance(case, dict) else None
        if not isinstance(audit, dict):
            continue
        compared += 1
        decision = evaluate_blocker(audit.get("candidate") or {}, audit.get("blocker") or {}, as_of=date(2026, 10, 25))
        blocked = bool(decision.get("blocks"))
        should_block = audit.get("class") == "LEGIT"
        repo_id = str(case.get("repo_id") or "")
        if blocked and not should_block:
            over.append(repo_id)
        if should_block and not blocked:
            false.append(repo_id)
    return {
        "set_size": compared,
        "over_refusal_count": len(over),
        "over_refusal_rate": (len(over) / compared) if compared else None,
        "false_pass_count": len(false),
        "false_pass_rate": (len(false) / compared) if compared else None,
        "over_refusal": over,
        "false_pass": false,
        "note": (
            "Deterministic maturity rule on the 16-case audit dated 2026-10-25. "
            "An observation on this small set, not a live Nightshift measurement and not a rate for the other curated repositories."
        ),
    }


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
        "rule_observation": None,
        "live_error_rates": None,
        "note": "",
    }
    if errors:
        report["note"] = "Label file failed validation. The live judge was not run."
        return report
    report["rule_observation"] = rule_observation(payload)
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
    by_id = {case["repo_id"]: case for case in payload["cases"]}
    live_over: list[str] = []
    live_false: list[str] = []
    live_n = 0
    for row in report["results"]:
        case = by_id.get(row["repo_id"]) or {}
        if not case.get("audit"):
            continue
        if not row.get("verdict"):
            continue
        live_n += 1
        acceptable = set(case.get("acceptable") or [])
        if row["verdict"] == "ALREADY_EXISTS" and "ALREADY_EXISTS" not in acceptable:
            live_over.append(row["repo_id"])
        if acceptable == {"ALREADY_EXISTS"} and row["verdict"] != "ALREADY_EXISTS":
            live_false.append(row["repo_id"])
    report["live_error_rates"] = {
        "set_size": live_n,
        "over_refusal_count": len(live_over),
        "over_refusal_rate": (len(live_over) / live_n) if live_n else None,
        "false_pass_count": len(live_false),
        "false_pass_rate": (len(live_false) / live_n) if live_n else None,
        "over_refusal": live_over,
        "false_pass": live_false,
        "note": "Live Nightshift verdicts on the audit cases in this run. Not a stored accuracy.",
    }
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
    from app.console import ensure_utf8_stdio

    ensure_utf8_stdio()
    raise SystemExit(main())
