"""MemoryStoreService: tiered persistence, CRUD, decay, promotion, backup."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from app.engines.hmem.ledger import TrustLedgerService
from app.engines.hmem.types import (
    DAY_MS,
    ENTITY_CAP,
    LAYER_WEIGHT,
    RECENCY_DAYS,
    RELATED_CAP,
    MemoryLayer,
    MemoryRecord,
    MemoryType,
    Provenance,
    ProvenanceOrigin,
)


class HashEmbedder:
    """Deterministic stand-in embedder. Swap for a model embedder without changing the store."""

    def __init__(self, dimensions: int = 32) -> None:
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in _tokens(text):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            vector[int.from_bytes(digest[:4], "big") % self.dimensions] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


def _tokens(text: str) -> list[str]:
    cleaned = []
    current = []
    for char in text.lower():
        if char.isalnum() or char in {".", "/", "-"}:
            current.append(char)
        elif current:
            cleaned.append("".join(current))
            current = []
    if current:
        cleaned.append("".join(current))
    return [token for token in cleaned if len(token) > 1]


class _FileLock:
    """Exclusive lock on a sibling file. Re-entrant for this object, blocking across processes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None
        self.depth = 0

    def __enter__(self) -> "_FileLock":
        if self.depth == 0:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "a+b")
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0, os.SEEK_END)
                    if handle.tell() == 0:
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except Exception:
                handle.close()
                raise
            self._handle = handle
        self.depth += 1
        return self

    def __exit__(self, *exc: object) -> bool:
        self.depth -= 1
        if self.depth == 0 and self._handle is not None:
            try:
                if os.name == "nt":
                    import msvcrt

                    self._handle.seek(0)
                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            finally:
                self._handle.close()
                self._handle = None
        return False


class MemoryStoreService:
    def __init__(
        self,
        *,
        ledger: TrustLedgerService,
        path: Path | None = None,
        clock=None,
        embedder: HashEmbedder | None = None,
        actor: str = "phoenixrepo",
    ) -> None:
        self.ledger = ledger
        self.path = path
        self.clock = clock or (lambda: 0)
        self.embedder = embedder or HashEmbedder()
        self.actor = actor
        self.records: dict[str, MemoryRecord] = {}
        self._lock = _FileLock(path.with_name(path.name + ".lock")) if path is not None else None

    @contextmanager
    def exclusive(self):
        """Hold the store lock and reload from disk once for a read-modify-write.

        Nested calls on this same object do not reload, so a batch can add several
        records without dropping the ones it just wrote. A second process blocks.
        """

        if self._lock is None:
            yield self
            return
        outer = self._lock.depth == 0
        with self._lock:
            if outer:
                self._reload()
            yield self

    def _reload(self) -> None:
        if self.path is None:
            return
        if not self.path.exists():
            self.records = {}
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            self.records = {
                item["id"]: MemoryRecord.model_validate(item) for item in payload.get("records", [])
            }
        except Exception:
            # A torn or corrupt file must not leave gate 1 with no memory forever.
            backup = self.path.with_name(self.path.name + ".bak")
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            self.records = {}

    def hydrate(self) -> int:
        with self.exclusive():
            self._apply_forgetting()
            self._save()
            return len(self.records)

    def add(
        self,
        content: str,
        *,
        source: str,
        origin: ProvenanceOrigin,
        label: str,
        layer: MemoryLayer = "working",
        memory_type: MemoryType = "factual",
        domain: str = "repository",
        namespace: str = "phoenixrepo",
        tags: list[str] | None = None,
        entities: list[str] | None = None,
        confidence: float = 0.9,
        refs: list[str] | None = None,
        derived_from: list[str] | None = None,
        goal_run_id: str | None = None,
        extra: dict | None = None,
        memory_id: str | None = None,
    ) -> MemoryRecord:
        if not content.strip():
            raise ValueError("H-MEM refuses an empty memory.")
        if not source.strip():
            raise ValueError("H-MEM requires source.")
        with self.exclusive():
            return self._add_loaded(
                content,
                source=source,
                origin=origin,
                label=label,
                layer=layer,
                memory_type=memory_type,
                domain=domain,
                namespace=namespace,
                tags=tags,
                entities=entities,
                confidence=confidence,
                refs=refs,
                derived_from=derived_from,
                goal_run_id=goal_run_id,
                extra=extra,
                memory_id=memory_id,
            )

    def _add_loaded(
        self,
        content: str,
        *,
        source: str,
        origin: ProvenanceOrigin,
        label: str,
        layer: MemoryLayer = "working",
        memory_type: MemoryType = "factual",
        domain: str = "repository",
        namespace: str = "phoenixrepo",
        tags: list[str] | None = None,
        entities: list[str] | None = None,
        confidence: float = 0.9,
        refs: list[str] | None = None,
        derived_from: list[str] | None = None,
        goal_run_id: str | None = None,
        extra: dict | None = None,
        memory_id: str | None = None,
    ) -> MemoryRecord:
        now = int(self.clock())
        record = MemoryRecord(
            id=memory_id or f"mem-{uuid4().hex[:12]}",
            content=content.strip(),
            type=memory_type,
            layer=layer,
            timestamp=now,
            last_accessed=now,
            tags=list(tags or []),
            embedding=self.embedder.embed(content),
            confidence=max(0.0, min(1.0, confidence)),
            source=source,
            provenance=Provenance(origin=origin, learned_at=now, label=label, refs=list(refs or [])),
            domain=domain,
            namespace=namespace,
            entities=list(entities or [])[:ENTITY_CAP],
            related_to=[],
            derived_from=list(derived_from or []),
            goal_run_id=goal_run_id,
            extra=dict(extra or {}),
        )
        self.records[record.id] = record
        self.ledger.append(record.id, "create", new_state={"layer": layer, "source": source})
        self._save()
        return record

    def get(self, memory_id: str) -> MemoryRecord | None:
        return self.records.get(memory_id)

    def all_records(self) -> list[MemoryRecord]:
        return list(self.records.values())

    def promote(self, memory_id: str, layer: MemoryLayer) -> MemoryRecord:
        with self.exclusive():
            record = self._require(memory_id)
            previous = record.layer
            record.layer = layer
            self.ledger.append(
                memory_id,
                "promote",
                previous_state={"layer": previous},
                new_state={"layer": layer},
            )
            self._save()
            return record

    def touch(self, memory_id: str) -> None:
        with self.exclusive():
            record = self._require(memory_id)
            record.last_accessed = int(self.clock())
            record.access_count += 1
            self.ledger.append(memory_id, "read", details={"access_count": record.access_count})
            self._save()

    def supersede(self, old_id: str, new_id: str) -> None:
        with self.exclusive():
            old = self._require(old_id)
            new = self._require(new_id)
            old.superseded_by = new_id
            new.supersedes = old_id
            if old_id not in new.related_to:
                new.related_to.append(old_id)
                new.related_to = new.related_to[:RELATED_CAP]
            self.ledger.append(
                old_id,
                "conflict_resolved",
                previous_state={"superseded_by": None},
                new_state={"superseded_by": new_id},
                details={"kept": new_id},
            )
            self._save()

    def apply_feedback(self, memory_id: str, kind: str) -> MemoryRecord:
        from app.engines.hmem.types import FEEDBACK_DELTAS

        if kind not in FEEDBACK_DELTAS:
            raise ValueError(f"Unknown feedback kind: {kind}")
        with self.exclusive():
            record = self._require(memory_id)
            record.confidence = max(0.0, min(1.0, record.confidence + FEEDBACK_DELTAS[kind]))
            self.ledger.append(memory_id, "feedback", details={"kind": kind, "confidence": record.confidence})
            self._save()
            return record

    def backup(self) -> dict:
        return {"records": [record.model_dump(mode="json") for record in self.records.values()]}

    def find_verdicts(self, repo_id: str) -> list[MemoryRecord]:
        key = str(repo_id or "").casefold()
        return [
            record
            for record in self.records.values()
            if record.extra.get("kind") == "verdict"
            and any(str(entity).casefold() == key for entity in record.entities)
        ]

    def find_evidence_records(self, repo_id: str, claim_id: str) -> list[MemoryRecord]:
        """Current evidence memories for one claim. Older stores may contain duplicates."""

        return [
            record
            for record in self.records.values()
            if not record.superseded_by
            and record.extra.get("kind") == "evidence"
            and record.extra.get("claim_id") == claim_id
            and repo_id in record.entities
        ]

    def find_evidence(self, repo_id: str, claim_id: str) -> MemoryRecord | None:
        matches = self.find_evidence_records(repo_id, claim_id)
        return matches[0] if matches else None

    def refresh(
        self,
        memory_id: str,
        *,
        content: str,
        confidence: float,
        goal_run_id: str | None,
        refs: list[str],
    ) -> MemoryRecord:
        with self.exclusive():
            record = self._require(memory_id)
            record.content = content.strip()
            record.embedding = self.embedder.embed(record.content)
            record.confidence = max(0.0, min(1.0, confidence))
            record.last_accessed = int(self.clock())
            record.goal_run_id = goal_run_id
            record.provenance.refs = list(refs)
            self.ledger.append(
                memory_id,
                "update",
                new_state={"confidence": record.confidence, "goal_run_id": goal_run_id},
            )
            self._save()
            return record

    def _apply_forgetting(self) -> None:
        now = int(self.clock())
        for record in self.records.values():
            if record.layer == "long_term":
                continue
            age_days = max(0.0, (now - record.last_accessed) / DAY_MS)
            decay = math.exp(-age_days / RECENCY_DAYS)
            record.confidence = max(0.05, min(1.0, record.confidence * decay))

    def _require(self, memory_id: str) -> MemoryRecord:
        record = self.records.get(memory_id)
        if record is None:
            raise KeyError(memory_id)
        return record

    def _save(self) -> None:
        """Replace the JSON file in one step. A crash leaves the previous file intact."""

        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.backup(), indent=2, ensure_ascii=False) + "\n"
        fd, name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        except Exception:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise


# Imported by retrieval for the documented layer weights.
assert set(LAYER_WEIGHT) == {"long_term", "working", "short_term"}
