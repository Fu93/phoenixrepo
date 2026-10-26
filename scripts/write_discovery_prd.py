"""Write the plan-only discovery PRD for a candidate the filter marks CANDIDATE."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.zetaris import result_content_hash  # noqa: E402
from app.agents.discovery_planner import (  # noqa: E402
    build_discovery_prd,
    prd_basename,
    render_discovery_markdown,
    render_json,
)
from app.agents.market_filter import decide_discovery  # noqa: E402
from app.sql_catalog import (  # noqa: E402
    DEMAND_COLUMNS,
    DISCOVERY_COLUMNS,
    PRIOR_ART_COLUMNS,
    demand_sql,
    discovery_sql,
    prior_art_sql,
)


def _read(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != columns:
            raise SystemExit(f"{path} columns do not match {columns}")
        return [dict(row) for row in reader]


def _hash(columns: tuple[str, ...], rows: list[dict[str, str]]) -> str:
    return result_content_hash(list(columns), rows)


def build_for_repo(repo_id: str, data_dir: Path) -> dict:
    candidates = [row for row in _read(data_dir / "discovery_candidates.csv", DISCOVERY_COLUMNS) if row["repo_id"] == repo_id]
    if not candidates:
        raise SystemExit(f"{repo_id} is not in discovery_candidates.csv")
    prior_rows = [row for row in _read(data_dir / "prior_art.csv", PRIOR_ART_COLUMNS) if row["repo_id"] == repo_id]
    demand_rows = [row for row in _read(data_dir / "demand_signals.csv", DEMAND_COLUMNS) if row["repo_id"] == repo_id]
    decision, reason = decide_discovery(candidates[0], prior_rows, demand_rows)
    if decision != "CANDIDATE":
        raise SystemExit(f"{repo_id} is {decision}: {reason}. No productise plan is written.")
    queries = {
        "discovery": {
            "query_id": "snapshot",
            "sql": discovery_sql(repo_id),
            "content_hash": _hash(DISCOVERY_COLUMNS, candidates),
        },
        "prior_art": {
            "query_id": "snapshot",
            "sql": prior_art_sql(repo_id),
            "content_hash": _hash(PRIOR_ART_COLUMNS, prior_rows),
        },
        "demand": {
            "query_id": "snapshot",
            "sql": demand_sql(repo_id),
            "content_hash": _hash(DEMAND_COLUMNS, demand_rows),
        },
    }
    return build_discovery_prd(candidates[0], prior_rows, demand_rows, queries)


def write_prd(repo_id: str, data_dir: Path, dest: Path) -> tuple[Path, Path]:
    document = build_for_repo(repo_id, data_dir)
    dest.mkdir(parents=True, exist_ok=True)
    base = prd_basename(repo_id)
    markdown = dest / f"{base}.prd.md"
    payload = dest / f"{base}.prd.json"
    markdown.write_text(render_discovery_markdown(document), encoding="utf-8")
    payload.write_text(render_json(document), encoding="utf-8")
    return markdown, payload


def main() -> int:
    repo_id = sys.argv[1] if len(sys.argv) > 1 else "SergioMendolia/ytkb"
    write_prd(repo_id, ROOT / "data" / "real", ROOT / "docs" / "revival")
    print(f"wrote discovery PRD for {repo_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
