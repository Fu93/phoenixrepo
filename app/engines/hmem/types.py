"""Canonical H-MEM shapes used by this implementation.

Follows engines/hmem/reference/src/types.ts and the retrieval formula in
engines/hmem/docs/retrieval-ranking.md (fetched 22 Oct 2026).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MemoryType = Literal["personal", "factual", "preference", "general"]
MemoryLayer = Literal["short_term", "working", "long_term"]
ProvenanceOrigin = Literal[
    "chat_message",
    "user_correction",
    "file_save",
    "file_open",
    "file_edit",
    "model_response",
    "plan_completion",
    "document",
    "manual",
    "dream",
    "sleep_synthesis",
]
LedgerAction = Literal[
    "create",
    "read",
    "update",
    "delete",
    "feedback",
    "promote",
    "demote",
    "merge",
    "conflict_detected",
    "conflict_resolved",
    "sleep_consolidate",
    "sleep_archive",
    "sleep_synthesize",
    "dream_proposed",
    "dream_approved",
    "dream_rejected",
    "restore",
]

LAYER_WEIGHT = {"long_term": 1.0, "working": 0.8, "short_term": 0.6}
FEEDBACK_DELTAS = {"helpful": 0.05, "not_helpful": -0.03, "wrong": -0.2}
DEFAULT_TOP_N = 5
DEFAULT_THRESHOLD = 0.35
ENTITY_CAP = 10
RELATED_CAP = 5
RECENCY_DAYS = 14
DAY_MS = 24 * 60 * 60 * 1000


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


class Provenance(BaseModel):
    origin: ProvenanceOrigin
    learned_at: int
    label: str
    refs: list[str] = Field(default_factory=list)


class MemoryRecord(BaseModel):
    id: str
    content: str
    type: MemoryType = "factual"
    layer: MemoryLayer = "working"
    timestamp: int
    last_accessed: int
    access_count: int = 0
    tags: list[str] = Field(default_factory=list)
    embedding: list[float] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    source: str
    provenance: Provenance
    domain: str = "repository"
    namespace: str = "phoenixrepo"
    entities: list[str] = Field(default_factory=list)
    related_to: list[str] = Field(default_factory=list)
    supersedes: str | None = None
    superseded_by: str | None = None
    derived_from: list[str] = Field(default_factory=list)
    chat_id: str | None = None
    mission_id: str | None = None
    goal_run_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class LedgerEntry(BaseModel):
    memory_id: str
    action: LedgerAction
    actor: str
    timestamp: int
    previous_state: dict[str, Any] | None = None
    new_state: dict[str, Any] | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RetrievedMemory(BaseModel):
    memory: MemoryRecord
    relevance: float
    signals: dict[str, float] = Field(default_factory=dict)


class QueryTrace(BaseModel):
    retrieval_reason: str
    strategy: Literal["minimal", "personal", "comprehensive"]
    scores: dict[str, float]


class QueryResult(BaseModel):
    memories: list[RetrievedMemory]
    context: str
    trace: QueryTrace
