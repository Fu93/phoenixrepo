#!/usr/bin/env python3
"""Check every RECORDED replay in data/replays/manifest.json.

A manifest entry is in scope when trace_class is RECORDED. SAMPLE seals are left
alone. For each one this calls scripts.verify_trace.verify with the run hash in
record.json (recorded_run.run_hash). It also applies the same Nemotron check as
verify_trace.py --require-nemotron when recorded_run.llm_used is true, and when
that key is absent and the trace has a used model answer. Pass or fail comes only
from those two existing functions. Served-model counts in the table use the same
"answer used" skip (status error or rejected) and do not decide the result.

Exit 0 when every checked replay passes. Exit 1 when any fails, or when the
manifest lists no RECORDED replay.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verify_trace import _NOT_USED, nemotron_report, verify

COLUMNS = ("replay", "verdict", "run_hash", "answers", "served_models", "result")


def _load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def served_model_counts(path: Path) -> dict[str, int]:
    """Count served_model on lines nemotron_report would count as used. Display only."""

    counts: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if "served_model" not in record:
            continue
        if str(record.get("status") or "") in _NOT_USED:
            continue
        served = record.get("served_model")
        name = str(served) if served else "(missing)"
        counts[name] = counts.get(name, 0) + 1
    return counts


def _answers(report: dict) -> str:
    split = ", ".join(f"{name} {count}" for name, count in report["providers"].items()) or "none"
    return f"{report['answers_by_nemotron']}/{report['answers_used']} nemotron; {split}"


def _served(counts: dict[str, int]) -> str:
    if not counts:
        return "-"
    return ", ".join(f"{name} {count}" for name, count in sorted(counts.items()))


def check_replay(replay_id: str, directory: Path) -> dict[str, str]:
    """One row. result is PASS, or FAIL and the reason."""

    row = {
        "replay": replay_id,
        "verdict": "",
        "run_hash": "",
        "answers": "-",
        "served_models": "-",
        "result": "FAIL",
    }
    record_path = directory / "record.json"
    trace_path = directory / "trace.jsonl"
    if not record_path.is_file():
        row["result"] = "FAIL missing record.json"
        return row
    try:
        record = _load_json(record_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        row["result"] = f"FAIL record.json: {exc}"
        return row
    verdict = record.get("verdict") if isinstance(record.get("verdict"), dict) else {}
    row["verdict"] = str(verdict.get("verdict") or "")
    recorded = record.get("recorded_run") if isinstance(record.get("recorded_run"), dict) else {}
    expected = str(recorded.get("run_hash") or "")
    row["run_hash"] = expected
    llm_used = recorded.get("llm_used")
    if not expected:
        row["result"] = "FAIL record.json has no recorded_run.run_hash"
        return row
    if not trace_path.is_file():
        row["result"] = "FAIL missing trace.jsonl"
        return row
    ok, detail = verify(trace_path, expected_run_hash=expected)
    reasons: list[str] = []
    if not ok:
        reasons.append(detail)
    try:
        report = nemotron_report(trace_path)
        counts = served_model_counts(trace_path)
    except (OSError, json.JSONDecodeError) as exc:
        row["result"] = "FAIL " + "; ".join(reasons + [f"could not read answers: {exc}"])
        return row
    row["answers"] = _answers(report)
    row["served_models"] = _served(counts)
    if llm_used is False:
        require = False
    elif llm_used is True:
        require = True
    else:
        require = report["answers_used"] > 0
    if require:
        if report["not_nemotron"]:
            reasons.append("not nemotron: " + "; ".join(report["not_nemotron"]))
        if report["answers_used"] == 0:
            reasons.append("no model answer in this trace")
    row["result"] = "PASS" if not reasons else "FAIL " + "; ".join(reasons)
    return row


def recorded_replays(manifest: dict) -> list[dict]:
    replays = manifest.get("replays")
    if not isinstance(replays, list):
        return []
    return [item for item in replays if isinstance(item, dict) and item.get("trace_class") == "RECORDED"]


def _print_table(rows: list[dict[str, str]]) -> None:
    widths = {name: len(name) for name in COLUMNS}
    for row in rows:
        for name in COLUMNS:
            widths[name] = max(widths[name], len(row[name]))
    header = "  ".join(name.ljust(widths[name]) for name in COLUMNS)
    print(header)
    print("  ".join("-" * widths[name] for name in COLUMNS))
    for row in rows:
        print("  ".join(row[name].ljust(widths[name]) for name in COLUMNS))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify every RECORDED replay against its record.json run hash.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=ROOT / "data" / "replays" / "manifest.json",
        help="Catalog manifest. Replay directories are read next to this file.",
    )
    args = parser.parse_args(argv)
    manifest_path = args.manifest
    if not manifest_path.is_file():
        print(f"FAIL manifest not found: {manifest_path}")
        return 1
    try:
        manifest = _load_json(manifest_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"FAIL manifest: {exc}")
        return 1
    selected = recorded_replays(manifest)
    if not selected:
        print("FAIL no RECORDED replay in the manifest")
        return 1
    root = manifest_path.parent
    rows: list[dict[str, str]] = []
    for item in selected:
        replay_id = str(item.get("id") or "")
        if not replay_id:
            rows.append({name: "" for name in COLUMNS} | {"replay": "(missing id)", "result": "FAIL manifest entry has no id"})
            continue
        rows.append(check_replay(replay_id, root / replay_id))
    _print_table(rows)
    failed = [row for row in rows if not row["result"].startswith("PASS")]
    print(f"{len(rows) - len(failed)} passed, {len(failed)} failed, {len(rows)} RECORDED")
    return 1 if failed else 0


if __name__ == "__main__":
    from app.console import prepare_cli

    prepare_cli()
    sys.exit(main())
