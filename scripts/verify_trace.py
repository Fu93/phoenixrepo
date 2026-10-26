"""Recompute a trace hash chain.

The chain shows each JSONL line still matches the hash written with it.
It does not show that the words in the line are true. It proves integrity
only against the recorded run hash: pass --expected-run-hash to require the
RUN_SEAL line_hash to be that value. Exit 0 when the chain matches. Exit 1
when a line was altered, the previous-hash link breaks, or the run hash differs.

--require-nemotron also fails unless every model answer the run used (an LLM line whose
status is not error or rejected) has a served_model in the nvidia/nemotron-* family, and
prints how many answers were used and which provider wrote them. served_model and the
provider are inside each line's hash, so a line edited to say Nemotron fails the chain first.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.trace_log import canonical_body, line_hash
from config.settings import is_nemotron_family

_NOT_USED = {"error", "rejected"}


def _provider(summary: object) -> str:
    if isinstance(summary, str):
        try:
            summary = json.loads(summary)
        except json.JSONDecodeError:
            return "nvidia-direct" if '"provider": "nvidia-direct"' in summary else "nightshift"
    if isinstance(summary, dict):
        return str(summary.get("provider") or "nightshift")
    return "nightshift"


def nemotron_report(path: Path) -> dict:
    """Count the model answers a trace used, by family and by provider. Call after verify()."""

    used = nemotron = discarded = 0
    providers: dict[str, int] = {}
    others: list[str] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        if "served_model" not in record:
            continue
        if str(record.get("status") or "") in _NOT_USED:
            if "DISCARDED_BY_POLICY" in (record.get("flags") or []):
                discarded += 1
            continue
        used += 1
        served = record.get("served_model")
        provider = _provider(record.get("output_summary"))
        providers[provider] = providers.get(provider, 0) + 1
        if is_nemotron_family(served):
            nemotron += 1
        else:
            others.append(f"line {index}: {served or '(missing)'}")
    return {"answers_used": used, "answers_by_nemotron": nemotron, "discarded_by_policy": discarded,
            "providers": dict(sorted(providers.items())), "not_nemotron": others}


def verify(path: Path, expected_run_hash: str | None = None) -> tuple[bool, str]:
    text = path.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False, "trace is empty"
    previous = ""
    for index, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            return False, f"line {index} is not JSON: {exc}"
        if not isinstance(record, dict):
            return False, f"line {index} is not an object"
        claimed = record.get("line_hash")
        linked = record.get("prev_hash")
        if linked != previous:
            return False, f"line {index} prev_hash does not match the previous line_hash"
        recomputed = line_hash(record)
        if claimed != recomputed:
            return False, f"line {index} line_hash does not match canonical JSON"
        # canonical_body is what was hashed; keep the import used so a refactor stays obvious.
        if not canonical_body(record):
            return False, f"line {index} has an empty body"
        previous = str(claimed)
    last = json.loads(lines[-1])
    if last.get("action") != "RUN_SEAL":
        return False, "the last line is not RUN_SEAL"
    run_hash = str(last.get("line_hash"))
    if expected_run_hash and expected_run_hash != run_hash:
        return False, f"run hash {run_hash} does not match the expected run hash"
    return True, run_hash


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a PhoenixRepo trace hash chain.")
    parser.add_argument("trace")
    parser.add_argument(
        "--expected-run-hash",
        default="",
        help="Fail unless RUN_SEAL line_hash equals this recorded run hash.",
    )
    parser.add_argument(
        "--require-nemotron",
        action="store_true",
        help="Also fail unless every model answer the run used was served by nvidia/nemotron-*.",
    )
    args = parser.parse_args(argv)
    ok, detail = verify(Path(args.trace), expected_run_hash=args.expected_run_hash or None)
    if not ok:
        print(detail)
        return 1
    print(detail)
    if args.require_nemotron:
        report = nemotron_report(Path(args.trace))
        split = ", ".join(f"{name} {count}" for name, count in report["providers"].items()) or "none"
        print(
            f"nemotron: {report['answers_by_nemotron']} of {report['answers_used']} answers used "
            f"(providers: {split}; discarded by policy: {report['discarded_by_policy']})"
        )
        if report["not_nemotron"]:
            print("not nemotron: " + "; ".join(report["not_nemotron"]))
            return 1
        if report["answers_used"] == 0:
            print("no model answer in this trace")
            return 1
    return 0


if __name__ == "__main__":
    from app.console import prepare_cli

    prepare_cli()
    sys.exit(main())
