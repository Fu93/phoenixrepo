"""Write the deterministic revival PRD from the committed snapshot. No network. No LLM."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.planner import build_revival_prd, evidence_context, prd_basename, render_json, render_markdown  # noqa: E402
from app.agents.policy import normalize_attempt, normalize_profile, normalize_signal, normalize_source  # noqa: E402
from app.sql_catalog import ATTEMPT_COLUMNS, PROFILE_COLUMNS, SIGNAL_COLUMNS, SOURCE_COLUMNS  # noqa: E402


def _read(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != columns:
            raise SystemExit(f"{path} columns do not match {columns}")
        return [dict(row) for row in reader]


def write_prd(repo_id: str, data_dir: Path, dest: Path) -> tuple[Path, Path]:
    profiles = [normalize_profile(row) for row in _read(data_dir / "repo_evidence.csv", PROFILE_COLUMNS)]
    row = next((item for item in profiles if item["repo_id"] == repo_id), None)
    if row is None:
        raise SystemExit(f"{repo_id} is not in repo_evidence.csv")
    signals = [
        normalize_signal(item)
        for item in _read(data_dir / "revival_signals.csv", SIGNAL_COLUMNS)
        if item["repo_id"] == repo_id
    ]
    attempts = [
        normalize_attempt(item)
        for item in _read(data_dir / "revival_attempts.csv", ATTEMPT_COLUMNS)
        if item["repo_id"] == repo_id
    ]
    sources = [
        normalize_source(item)
        for item in _read(data_dir / "revival_sources.csv", SOURCE_COLUMNS)
        if item["repo_id"] == repo_id
    ]
    document = build_revival_prd(row, signals, attempts, sources, evidence_context(repo_id, signals, attempts, sources))
    dest.mkdir(parents=True, exist_ok=True)
    base = prd_basename(repo_id)
    markdown_path = dest / f"{base}.prd.md"
    json_path = dest / f"{base}.prd.json"
    markdown_path.write_text(render_markdown(document), encoding="utf-8")
    json_path.write_text(render_json(document), encoding="utf-8")
    return markdown_path, json_path


def main() -> int:
    repo_id = sys.argv[1] if len(sys.argv) > 1 else "jondot/hygen"
    markdown_path, json_path = write_prd(repo_id, ROOT / "data" / "real", ROOT / "docs" / "revival")
    print(markdown_path)
    print(json_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
