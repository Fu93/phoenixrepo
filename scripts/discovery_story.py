"""Print the offline idea-filter verdicts from the committed CSVs. No network."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.market_filter import decide_discovery, showcase_prior_art_note  # noqa: E402
from app.sql_catalog import DEMAND_COLUMNS, DISCOVERY_COLUMNS, PRIOR_ART_COLUMNS  # noqa: E402

SHOWCASE = "jondot/hygen"


def _rows(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != columns:
            raise SystemExit(f"{path} columns do not match the view contract")
        return [dict(row) for row in reader]


def main() -> int:
    data = ROOT / "data" / "real"
    candidates = _rows(data / "discovery_candidates.csv", DISCOVERY_COLUMNS)
    prior = _rows(data / "prior_art.csv", PRIOR_ART_COLUMNS)
    demand = _rows(data / "demand_signals.csv", DEMAND_COLUMNS)
    for candidate in candidates:
        repo_id = candidate["repo_id"]
        decision, reason = decide_discovery(
            candidate,
            [row for row in prior if row["repo_id"] == repo_id],
            [row for row in demand if row["repo_id"] == repo_id],
        )
        print(f"{repo_id} {decision}")
        print(reason)
        print(f"stars_recorded {candidate['stargazers_count']} ignored")
    showcase = [row for row in prior if row["repo_id"] == SHOWCASE]
    if showcase:
        print(f"{SHOWCASE} revival-set")
        print(showcase_prior_art_note(showcase))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
