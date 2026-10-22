"""Recompute a trace hash chain.

The chain shows each JSONL line still matches the hash written with it.
It does not show that the words in the line are true. Exit 0 when the chain
matches. Exit 1 when a line was altered or the previous-hash link breaks.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.trace_log import canonical_body, line_hash


def verify(path: Path) -> tuple[bool, str]:
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
    return True, str(last.get("line_hash"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a PhoenixRepo trace hash chain.")
    parser.add_argument("trace")
    args = parser.parse_args()
    ok, detail = verify(Path(args.trace))
    if not ok:
        print(detail)
        return 1
    print(detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
