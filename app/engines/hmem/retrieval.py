"""MemoryRetrievalService: hybrid ranking, context packaging, retrieval trace."""

from __future__ import annotations

import math

from app.engines.hmem.store import MemoryStoreService, _tokens
from app.engines.hmem.types import (
    DAY_MS,
    DEFAULT_THRESHOLD,
    DEFAULT_TOP_N,
    LAYER_WEIGHT,
    RECENCY_DAYS,
    MemoryRecord,
    QueryResult,
    QueryTrace,
    RetrievedMemory,
    clamp01,
)


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left)) or 1.0
    right_norm = math.sqrt(sum(b * b for b in right)) or 1.0
    return max(0.0, min(1.0, dot / (left_norm * right_norm)))


def top_tokens(text: str, limit: int = 8) -> list[str]:
    counts: dict[str, int] = {}
    for token in _tokens(text):
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts, key=lambda token: (-counts[token], token))
    return ranked[:limit]


def jaccard(left: list[str], right: list[str]) -> float:
    a = set(left)
    b = set(right)
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def infer_domain(text: str) -> str:
    if "repository" in text.lower() or "repo" in _tokens(text):
        return "repository"
    return "general"


def blend_score(signals: dict[str, float], confidence: float) -> float:
    """Canonical eight-signal blend, then multiplied by confidence."""

    raw = (
        0.35 * signals["semantic"]
        + 0.20 * signals["keyword"]
        + 0.10 * signals["tag"]
        + 0.10 * signals["domain"]
        + 0.15 * signals["entity"]
        + 0.05 * signals["layer"]
        + 0.05 * signals["recency"]
        - 0.20 * signals["superseded"]
    )
    return clamp01(raw) * confidence


class MemoryRetrievalService:
    def __init__(self, store: MemoryStoreService) -> None:
        self.store = store

    def query(
        self,
        text: str,
        *,
        top_n: int = DEFAULT_TOP_N,
        threshold: float = DEFAULT_THRESHOLD,
        strategy: str = "comprehensive",
        entities: list[str] | None = None,
        tags: list[str] | None = None,
        kind: str | None = None,
        require_entities: bool = False,
        include_superseded: bool = True,
    ) -> QueryResult:
        """Rank memories. ``kind``, required entities, and superseded status are applied before top-k."""

        if strategy not in {"minimal", "personal", "comprehensive"}:
            raise ValueError(f"Unknown retrieval strategy: {strategy}")
        query_embedding = self.store.embedder.embed(text)
        query_tokens = top_tokens(text)
        query_domain = infer_domain(text)
        query_entities = list(entities or [])
        query_tags = list(tags or [])
        now = int(self.store.clock())
        candidates = self._candidates(
            kind=kind,
            entities=query_entities,
            require_entities=require_entities,
            include_superseded=include_superseded,
        )
        ranked: list[RetrievedMemory] = []
        for record in candidates:
            signals = self._signals(
                record,
                query_embedding=query_embedding,
                query_tokens=query_tokens,
                query_domain=query_domain,
                query_entities=query_entities,
                query_tags=query_tags,
                now=now,
            )
            score = blend_score(signals, record.confidence)
            if score < threshold:
                continue
            if strategy == "minimal" and record.layer == "short_term":
                continue
            ranked.append(RetrievedMemory(memory=record, relevance=round(score, 4), signals=signals))
        ranked.sort(key=lambda item: (-item.relevance, item.memory.id))
        chosen = ranked[:top_n]
        for item in chosen:
            self.store.touch(item.memory.id)
        return QueryResult(
            memories=chosen,
            context=format_reinjection(chosen),
            trace=QueryTrace(
                retrieval_reason=f"hybrid {strategy} over {len(candidates)} memories",
                strategy=strategy,  # type: ignore[arg-type]
                scores={item.memory.id: item.relevance for item in chosen},
            ),
        )

    def _candidates(
        self,
        *,
        kind: str | None,
        entities: list[str],
        require_entities: bool,
        include_superseded: bool,
    ) -> list[MemoryRecord]:
        required = set(entities)
        chosen: list[MemoryRecord] = []
        for record in self.store.all_records():
            if kind is not None and record.extra.get("kind") != kind:
                continue
            if require_entities and required and not required.issubset(record.entities):
                continue
            if not include_superseded and record.superseded_by:
                continue
            chosen.append(record)
        return chosen

    def _signals(
        self,
        record: MemoryRecord,
        *,
        query_embedding: list[float],
        query_tokens: list[str],
        query_domain: str,
        query_entities: list[str],
        query_tags: list[str],
        now: int,
    ) -> dict[str, float]:
        age_days = max(0.0, (now - record.last_accessed) / DAY_MS)
        recency = math.exp(-age_days / RECENCY_DAYS)
        entity_overlap = jaccard(query_entities, record.entities) if query_entities else (
            1.0 if any(entity.lower() in record.content.lower() for entity in record.entities) else 0.0
        )
        tag_overlap = jaccard(query_tags, record.tags) if query_tags else 0.0
        return {
            "semantic": cosine(query_embedding, record.embedding),
            "keyword": jaccard(query_tokens, top_tokens(record.content)),
            "tag": tag_overlap,
            "domain": 1.0 if record.domain == query_domain else 0.0,
            "entity": entity_overlap,
            "layer": LAYER_WEIGHT[record.layer],
            "recency": recency,
            "superseded": 1.0 if record.superseded_by else 0.0,
        }


def format_reinjection(memories: list[RetrievedMemory]) -> str:
    if not memories:
        return ""
    grouped: dict[str, list[str]] = {"long_term": [], "working": [], "short_term": []}
    for item in memories:
        grouped[item.memory.layer].append(
            f"- [{item.memory.id}] {item.memory.content} (score={item.relevance}, source={item.memory.source})"
        )
    lines = ["H-MEM recall:"]
    for layer in ("long_term", "working", "short_term"):
        if grouped[layer]:
            lines.append(f"[{layer}]")
            lines.extend(grouped[layer])
    return "\n".join(lines)
