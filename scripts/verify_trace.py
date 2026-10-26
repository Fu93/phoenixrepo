"""Recompute a trace hash chain, then reject a non-Nemotron served model.

The chain shows each JSONL line still matches the hash written with it.
It does not show that the words in the line are true. It proves integrity
only against the recorded run hash: pass --expected-run-hash to require the
RUN_SEAL line_hash to be that value. Exit 0 when the chain matches and every
recorded served model is in the nvidia/nemotron-* family (the sample stand-in
is allowed; a line with no served model is not a model answer). Exit 1 when a
line was altered, the previous-hash link breaks, the run hash differs, or a
served model is outside that family.

``verify()`` is the hash check alone. The replay page uses it so a recorded
MODEL_MISMATCH still shows a chain PASS. This command adds the family check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.trace_log import canonical_body, line_hash
from config.settings import model_mismatch


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


def _object(value: object) -> dict | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.startswith("{"):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(loaded, dict):
            return loaded
    return None


def _foreign_served(value: object) -> str | None:
    """A served id outside nvidia/nemotron-*, or None when the field is absent, blank, Nemotron, or the sample stand-in."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or not model_mismatch(text):
        return None
    return text


def foreign_served_model(path: Path) -> tuple[int, str] | None:
    """First line whose served_model is outside nvidia/nemotron-*, if the chain is not what this checks."""

    text = path.read_text(encoding="utf-8")
    for index, line in enumerate((row for row in text.splitlines() if row.strip()), start=1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(record, dict):
            continue
        candidates = [record.get("served_model")]
        for key in ("output_summary", "input_summary"):
            payload = _object(record.get(key))
            if payload is not None and "served_model" in payload:
                candidates.append(payload.get("served_model"))
        for value in candidates:
            foreign = _foreign_served(value)
            if foreign:
                return index, foreign
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a PhoenixRepo trace hash chain.")
    parser.add_argument("trace")
    parser.add_argument(
        "--expected-run-hash",
        default="",
        help="Fail unless RUN_SEAL line_hash equals this recorded run hash.",
    )
    args = parser.parse_args()
    path = Path(args.trace)
    ok, detail = verify(path, expected_run_hash=args.expected_run_hash or None)
    if not ok:
        print(detail)
        return 1
    foreign = foreign_served_model(path)
    if foreign:
        index, model = foreign
        print(f"line {index} served_model {model} is outside nvidia/nemotron-*")
        return 1
    print(detail)
    return 0


if __name__ == "__main__":
    from app.console import prepare_cli

    prepare_cli()
    sys.exit(main())
