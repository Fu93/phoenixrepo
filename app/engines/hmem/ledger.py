"""Append-only trust ledger. Entries are never updated or deleted."""

from __future__ import annotations

import json
from pathlib import Path

from app.engines.hmem.types import LedgerAction, LedgerEntry


class TrustLedgerService:
    def __init__(self, path: Path | None = None, *, clock, actor: str = "phoenixrepo") -> None:
        self.path = path
        self._clock = clock
        self.actor = actor
        self.entries: list[LedgerEntry] = []
        if path and path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    self.entries.append(LedgerEntry.model_validate_json(line))

    def append(
        self,
        memory_id: str,
        action: LedgerAction,
        *,
        actor: str | None = None,
        previous_state: dict | None = None,
        new_state: dict | None = None,
        details: dict | None = None,
    ) -> LedgerEntry:
        entry = LedgerEntry(
            memory_id=memory_id,
            action=action,
            actor=actor or self.actor,
            timestamp=int(self._clock()),
            previous_state=previous_state,
            new_state=new_state,
            details=details or {},
        )
        self.entries.append(entry)
        self._persist(entry)
        return entry

    def query(self, *, memory_id: str | None = None, action: str | None = None) -> list[LedgerEntry]:
        rows = self.entries
        if memory_id:
            rows = [row for row in rows if row.memory_id == memory_id]
        if action:
            rows = [row for row in rows if row.action == action]
        return list(rows)

    def export(self) -> list[dict]:
        return [entry.model_dump(mode="json") for entry in self.entries]

    def _persist(self, entry: LedgerEntry) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry.model_dump(mode="json"), ensure_ascii=False) + "\n")
