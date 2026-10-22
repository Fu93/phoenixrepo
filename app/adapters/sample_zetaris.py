"""Sample-mode Zetaris. Same SQL contract, rows from the labelled cache. No network."""

from __future__ import annotations

import csv
import re
import uuid
from pathlib import Path
from typing import Any

from app.adapters.zetaris import ZetarisResult, assert_single_statement
from app.errors import ZetarisError
from app.paths import repo_root
from app.sql_catalog import PROFILE_COLUMNS, SIGNAL_COLUMNS

_QUERY = re.compile(
    r"^select\s+(?P<cols>.+?)\s+from\s+phoenix\.(?P<view>repo_evidence|revival_signals)"
    r"\s+where\s+(?P<col>[a-z_]+)\s*=\s*'(?P<literal>(?:[^']|'')*)'\s*$",
    re.IGNORECASE | re.DOTALL,
)

_VIEWS = {
    "repo_evidence": ("repo_evidence.csv", PROFILE_COLUMNS),
    "revival_signals": ("revival_signals.csv", SIGNAL_COLUMNS),
}


class SampleZetaris:
    """Executes the agent's single-statement SELECTs against cached view output."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or (repo_root() / "data" / "sample")
        self._tables = {
            view: self._load(filename, columns)
            for view, (filename, columns) in _VIEWS.items()
        }

    def provider(self) -> str:
        return "zetaris"

    def configured(self) -> bool:
        return True

    def enabled(self) -> bool:
        return True

    def mode(self) -> str:
        return "sample-cache"

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider(),
            "configured": True,
            "enabled": True,
            "integrated": True,
            "integration": "SAMPLE_CACHE",
            "load_bearing": True,
            "mode": self.mode(),
            "cache_dir": str(self.cache_dir),
        }

    def run_sql(self, sql: str, limit: int = 100) -> ZetarisResult:
        statement = assert_single_statement(sql)
        match = _QUERY.match(statement)
        if not match:
            raise ZetarisError(
                "Sample Zetaris only runs a single SELECT against phoenix.repo_evidence "
                "or phoenix.revival_signals.",
                status_code=400,
            )
        view = match.group("view").lower()
        column = match.group("col").lower()
        literal = match.group("literal").replace("''", "'")
        selected = match.group("cols").strip()
        table = self._tables[view]
        if column not in table["columns"]:
            raise ZetarisError(f"Unknown column {column} on phoenix.{view}.", status_code=400)
        rows = [row for row in table["rows"] if str(row.get(column, "")) == literal]
        if selected != "*":
            names = [part.strip() for part in selected.split(",")]
            unknown = [name for name in names if name not in table["columns"]]
            if unknown:
                raise ZetarisError(
                    f"Unknown columns on phoenix.{view}: {', '.join(unknown)}.",
                    status_code=400,
                )
            rows = [{name: row[name] for name in names} for row in rows]
            columns = names
        else:
            columns = list(table["columns"])
        rows = rows[:limit]
        return ZetarisResult(
            query_id=str(uuid.uuid4()),
            sql=statement,
            columns=columns,
            rows=rows,
            source=f"phoenix.{view}",
            mode="sample-cache",
        )

    def _load(self, filename: str, expected: tuple[str, ...]) -> dict[str, Any]:
        path = self.cache_dir / filename
        if not path.exists():
            raise ZetarisError(f"Sample cache is missing {filename}.", status_code=500)
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            columns = tuple(reader.fieldnames or ())
            if columns != expected:
                raise ZetarisError(
                    f"{filename} columns {columns} do not match the phoenix view contract.",
                    status_code=500,
                )
            rows = [dict(row) for row in reader]
        if not rows:
            raise ZetarisError(f"{filename} has no rows.", status_code=500)
        return {"columns": columns, "rows": rows}
