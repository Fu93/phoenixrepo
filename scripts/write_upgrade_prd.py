"""Write the hygen upgrade PRD from the committed snapshot. No network. No LLM."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agents.upgrade_planner import build_upgrade_prd, render_upgrade_json, render_upgrade_markdown  # noqa: E402
from scripts.collect_hygen_upgrade import SNAPSHOT, assemble, RAW  # noqa: E402


def write_upgrade_prd(dest: Path, snapshot: dict | None = None) -> tuple[Path, Path]:
    document = build_upgrade_prd(snapshot if snapshot is not None else assemble(RAW))
    dest.mkdir(parents=True, exist_ok=True)
    markdown_path = dest / "jondot__hygen.upgrade.prd.md"
    json_path = dest / "jondot__hygen.upgrade.prd.json"
    markdown_path.write_text(render_upgrade_markdown(document), encoding="utf-8")
    json_path.write_text(render_upgrade_json(document), encoding="utf-8")
    return markdown_path, json_path


def main() -> int:
    markdown_path, json_path = write_upgrade_prd(ROOT / "docs" / "revival")
    print(markdown_path)
    print(json_path)
    print(SNAPSHOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
