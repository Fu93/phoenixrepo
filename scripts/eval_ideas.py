"""Run the idea filter in deterministic phrase/keyword mode and print accuracy.

Reads data/eval/cases.json. Does not call a model or the network.
Labels are the human judgements in that file. A miss is reported, not rewritten.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.market_filter import decide_discovery  # noqa: E402

CASES = ROOT / "data" / "eval" / "cases.json"
QUERIES = ROOT / "data" / "eval" / "queries.json"


def evaluate(path: Path = CASES) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = []
    for case in payload["cases"]:
        predicted, reason = decide_discovery(case["candidate"], case["prior_art"], case["demand"])
        results.append(
            {
                "repo_id": case["repo_id"],
                "label": case["label"],
                "predicted": predicted,
                "match": predicted == case["label"],
                "method": "phrase/keyword",
                "reason": reason,
            }
        )
    correct = sum(1 for item in results if item["match"])
    total = len(results)
    queries = json.loads(QUERIES.read_text(encoding="utf-8"))["queries"]
    return {
        "mode": "deterministic-phrase",
        "cases": total,
        "correct": correct,
        "accuracy": correct / total if total else 0,
        "query_count": len(queries),
        "results": results,
    }


def main() -> int:
    report = evaluate()
    json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
