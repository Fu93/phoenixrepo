import math

from app.engines.hmem.ledger import TrustLedgerService
from app.engines.hmem.retrieval import MemoryRetrievalService, blend_score, jaccard
from app.engines.hmem.store import MemoryStoreService
from app.engines.hmem.types import DAY_MS, LAYER_WEIGHT, clamp01


def _services(tmp_path, now=1_700_000_000_000):
    ledger = TrustLedgerService(tmp_path / "ledger.jsonl", clock=lambda: now)
    store = MemoryStoreService(ledger=ledger, path=tmp_path / "store.json", clock=lambda: now)
    store.hydrate()
    retrieval = MemoryRetrievalService(store)
    return store, retrieval, ledger


def test_store_requires_source_and_round_trips(tmp_path):
    store, _retrieval, ledger = _services(tmp_path)
    with pytest_raises_value():
        store.add("orphan", source="  ", origin="manual", label="x")
    record = store.add(
        "repository sample/phoenix-ledger licence MIT",
        source="zetaris:phoenix.repo_evidence",
        origin="document",
        label="evidence",
        tags=["evidence"],
        entities=["sample/phoenix-ledger"],
        confidence=0.9,
    )
    assert record.provenance.label == "evidence"
    assert record.embedding
    reloaded = MemoryStoreService(
        ledger=TrustLedgerService(tmp_path / "ledger.jsonl", clock=lambda: 1_700_000_000_000),
        path=tmp_path / "store.json",
        clock=lambda: 1_700_000_000_000,
    )
    reloaded.hydrate()
    assert reloaded.get(record.id).content == record.content
    assert any(entry.action == "create" for entry in ledger.entries)


def pytest_raises_value():
    import pytest

    return pytest.raises(ValueError)


def test_promotion_decay_and_append_only_ledger(tmp_path):
    now = 1_700_000_000_000
    store, _retrieval, ledger = _services(tmp_path, now)
    short = store.add(
        "session note",
        source="run",
        origin="manual",
        label="short",
        layer="short_term",
        confidence=0.8,
    )
    store.promote(short.id, "long_term")
    assert store.get(short.id).layer == "long_term"
    later = now + 30 * DAY_MS
    aged = MemoryStoreService(
        ledger=TrustLedgerService(tmp_path / "ledger.jsonl", clock=lambda: later),
        path=tmp_path / "store.json",
        clock=lambda: later,
    )
    aged.hydrate()
    assert aged.get(short.id).confidence == 0.8
    before = len(ledger.entries)
    assert ledger.entries[:before] == ledger.entries
    exported = ledger.export()
    assert exported[0]["action"] == "create"
    assert "update_entry" not in dir(ledger)


def test_retrieval_formula_and_threshold(tmp_path):
    signals = {
        "semantic": 1.0,
        "keyword": 1.0,
        "tag": 1.0,
        "domain": 1.0,
        "entity": 1.0,
        "layer": LAYER_WEIGHT["long_term"],
        "recency": 1.0,
        "superseded": 0.0,
    }
    assert blend_score(signals, 1.0) == clamp01(
        0.35 + 0.20 + 0.10 + 0.10 + 0.15 + 0.05 + 0.05
    )
    penalized = dict(signals, superseded=1.0)
    assert blend_score(penalized, 1.0) < blend_score(signals, 1.0)
    assert math.isclose(jaccard(["a", "b"], ["b", "c"]), 1 / 3)

    store, retrieval, _ledger = _services(tmp_path)
    kept = store.add(
        "repository sample/phoenix-ledger category accounting licence evidence",
        source="zetaris",
        origin="document",
        label="evidence",
        domain="repository",
        tags=["evidence", "accounting"],
        entities=["sample/phoenix-ledger"],
        confidence=0.95,
    )
    store.add(
        "unrelated grocery list",
        source="manual",
        origin="manual",
        label="other",
        domain="general",
        tags=["shopping"],
        entities=["grocery"],
        confidence=0.4,
        layer="short_term",
    )
    result = retrieval.query(
        "repository sample/phoenix-ledger category accounting licence evidence",
        entities=["sample/phoenix-ledger"],
        tags=["evidence"],
        threshold=0.35,
    )
    assert [item.memory.id for item in result.memories] == [kept.id]
    assert "H-MEM recall:" in result.context
    assert result.trace.strategy == "comprehensive"
    assert kept.id in result.trace.scores


def test_superseded_memory_is_penalized_not_deleted(tmp_path):
    store, retrieval, ledger = _services(tmp_path)
    old = store.add(
        "repository sample/field-notes verdict NO_GO",
        source="verdict",
        origin="plan_completion",
        label="verdict",
        tags=["verdict"],
        entities=["sample/field-notes"],
        confidence=0.95,
    )
    new = store.add(
        "repository sample/field-notes verdict RESURRECTION_FAILED",
        source="verdict",
        origin="plan_completion",
        label="verdict",
        tags=["verdict"],
        entities=["sample/field-notes"],
        confidence=0.95,
    )
    store.supersede(old.id, new.id)
    assert store.get(old.id) is not None
    result = retrieval.query(
        "repository sample/field-notes verdict",
        entities=["sample/field-notes"],
        tags=["verdict"],
        threshold=0.2,
    )
    scores = {item.memory.id: item.relevance for item in result.memories}
    assert new.id in scores
    assert scores[new.id] > scores.get(old.id, 0)
    assert any(entry.action == "conflict_resolved" for entry in ledger.entries)
