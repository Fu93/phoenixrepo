"""Normalise the committed discovery snapshot into Zetaris CSVs. No network.

The raw file is data/discovery/raw/snapshot.json. Numeric repo fields are copied
from the GitHub payloads stored there. Days are computed from pushed_at and
collected_at. This script does not call GitHub.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.market_filter import days_between  # noqa: E402
from app.sql_catalog import DEMAND_COLUMNS, DISCOVERY_COLUMNS, PRIOR_ART_COLUMNS  # noqa: E402

RAW = ROOT / "data" / "discovery" / "raw" / "snapshot.json"
REAL = ROOT / "data" / "real"
SAMPLE = ROOT / "data" / "sample"


def load_snapshot(path: Path = RAW) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_tables(snapshot: dict) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    collected_at = str(snapshot["collected_at"])
    origin = str(snapshot["data_origin"])
    repos = snapshot["repos"]
    candidates = []
    for spec in snapshot["candidates"]:
        repo = repos[spec["repo_id"]]
        licence = repo.get("license") or {}
        spdx = ""
        if isinstance(licence, dict):
            spdx = str(licence.get("spdx_id") or "")
            if spdx in {"NOASSERTION", "null", "None"}:
                spdx = ""
        row = {
            "repo_id": spec["repo_id"],
            "html_url": str(repo.get("html_url") or ""),
            "description": str(repo.get("description") or ""),
            "readme_excerpt": spec["readme_excerpt"],
            "problem_excerpt": spec["problem_excerpt"],
            "user_excerpt": spec["user_excerpt"],
            "scope_excerpt": spec["scope_excerpt"],
            "code_paths": "|".join(spec["code_paths"]),
            "created_at": str(repo.get("created_at") or ""),
            "pushed_at": str(repo.get("pushed_at") or ""),
            "days_since_push": str(_days(repo.get("pushed_at"), collected_at)),
            "stargazers_count": str(repo.get("stargazers_count")),
            "forks_count": str(repo.get("forks_count")),
            "open_issues": str(repo.get("open_issues_count")),
            "licence_spdx": spdx,
            "core_terms": "|".join(spec["core_terms"]),
            "cohort": spec.get("cohort") or "discovery",
            "data_origin": origin,
            "collected_at": collected_at,
        }
        candidates.append(row)

    prior_rows = []
    for spec in snapshot["prior_art"]:
        repo = repos[spec["alternative_id"]]
        licence = repo.get("license") or {}
        spdx = ""
        if isinstance(licence, dict):
            spdx = str(licence.get("spdx_id") or "")
        if spdx in {"NOASSERTION", "null", "None"}:
            spdx = ""
        prior_rows.append(
            {
                "repo_id": spec["repo_id"],
                "alternative_id": spec["alternative_id"],
                "url": str(repo.get("html_url") or ""),
                "licence_spdx": spdx,
                "stars": str(repo.get("stargazers_count")),
                "pushed_at": str(repo.get("pushed_at") or ""),
                "days_since_push": str(_days(repo.get("pushed_at"), collected_at)),
                "excerpt": spec["excerpt"],
                "candidate_excerpt": spec["candidate_excerpt"],
                "required_terms": "|".join(spec["required_terms"]),
                "data_origin": origin,
                "collected_at": collected_at,
            }
        )

    demand_rows = []
    for spec in snapshot["demand"]:
        alternative = repos.get(spec.get("alternative_id") or "")
        pushed = str((alternative or {}).get("pushed_at") or spec.get("pushed_at") or "")
        stars = spec.get("stars")
        if stars is None and alternative is not None:
            stars = alternative.get("stargazers_count")
        days = ""
        if pushed:
            days = str(_days(pushed, collected_at))
        demand_rows.append(
            {
                "repo_id": spec["repo_id"],
                "signal_id": spec["signal_id"],
                "kind": spec["kind"],
                "url": spec.get("url") or str((alternative or {}).get("html_url") or ""),
                "excerpt": spec.get("excerpt") or "",
                "metric_name": spec.get("metric_name") or "",
                "metric_value": "" if spec.get("metric_value") is None else str(spec.get("metric_value")),
                "comments": "" if spec.get("comments") is None else str(spec.get("comments")),
                "reactions": "" if spec.get("reactions") is None else str(spec.get("reactions")),
                "stars": "" if stars is None else str(stars),
                "pushed_at": pushed,
                "days_since_push": days,
                "query": spec.get("query") or "",
                "data_origin": origin,
                "collected_at": collected_at,
            }
        )
    _check_columns(candidates, DISCOVERY_COLUMNS)
    _check_columns(prior_rows, PRIOR_ART_COLUMNS)
    _check_columns(demand_rows, DEMAND_COLUMNS)
    return candidates, prior_rows, demand_rows


def provenance(snapshot: dict, raw_path: Path = RAW) -> dict:
    payload = raw_path.read_bytes()
    return {
        "collected_at": snapshot["collected_at"],
        "data_origin": snapshot["data_origin"],
        "token_used": False,
        "token_written": False,
        "commands": snapshot.get("commands") or [],
        "readme_sha256": snapshot.get("readme_sha256") or {},
        "raw_sha256": hashlib.sha256(payload).hexdigest(),
        "note": (
            "Days are the date difference between pushed_at and collected_at. "
            "Search total_count values are that query's total, not a census. "
            "README excerpts were checked as substrings of the fetched README bodies "
            "whose sha256 values are stored here. Those bodies are not committed."
        ),
    }


def write_outputs(root: Path = ROOT) -> None:
    snapshot = load_snapshot(root / "data" / "discovery" / "raw" / "snapshot.json")
    candidates, prior_rows, demand_rows = build_tables(snapshot)
    real = root / "data" / "real"
    sample = root / "data" / "sample"
    _write_csv(real / "discovery_candidates.csv", DISCOVERY_COLUMNS, candidates)
    _write_csv(real / "prior_art.csv", PRIOR_ART_COLUMNS, prior_rows)
    _write_csv(real / "demand_signals.csv", DEMAND_COLUMNS, demand_rows)
    _write_csv(sample / "discovery_candidates.csv", DISCOVERY_COLUMNS, [])
    _write_csv(sample / "prior_art.csv", PRIOR_ART_COLUMNS, [])
    _write_csv(sample / "demand_signals.csv", DEMAND_COLUMNS, [])
    (real / "discovery_provenance.json").write_text(
        json.dumps(provenance(snapshot, root / "data" / "discovery" / "raw" / "snapshot.json"), indent=2) + "\n",
        encoding="utf-8",
    )


def _days(pushed_at: str | None, collected_at: str) -> int:
    count = days_between(pushed_at, collected_at)
    if count is None:
        raise ValueError(f"cannot compute days for {pushed_at}")
    return count


def _check_columns(rows: list[dict[str, str]], columns: tuple[str, ...]) -> None:
    expected = set(columns)
    for row in rows:
        if set(row) != expected:
            missing = expected - set(row)
            extra = set(row) - expected
            raise ValueError(f"column mismatch missing={missing} extra={extra}")


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def main() -> int:
    write_outputs()
    print("wrote discovery_candidates.csv prior_art.csv demand_signals.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
