import math
import os
import threading

import pytest

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


def test_verdict_kind_is_selected_before_top_k(tmp_path):
    store, retrieval, _ledger = _services(tmp_path)
    for index in range(15):
        store.add(
            f"repository sample/field-notes category research licence evidence claim {index}",
            source="zetaris",
            origin="document",
            label="evidence",
            tags=["evidence", "research"],
            entities=["sample/field-notes"],
            confidence=0.99,
            extra={"kind": "evidence", "claim_id": f"CLM-{index}"},
        )
    verdict = store.add(
        "repository sample/field-notes verdict RESURRECTION_FAILED",
        source="verdict",
        origin="plan_completion",
        label="verdict",
        layer="long_term",
        tags=["verdict"],
        entities=["sample/field-notes"],
        confidence=0.95,
        extra={"kind": "verdict", "decision": "RESURRECTION_FAILED"},
    )
    result = retrieval.query(
        "repository sample/field-notes verdict",
        entities=["sample/field-notes"],
        tags=["verdict"],
        kind="verdict",
        require_entities=True,
        include_superseded=False,
        top_n=1,
        threshold=0.0,
    )
    assert [item.memory.id for item in result.memories] == [verdict.id]


def test_repeated_evidence_refreshes_one_memory(tmp_path):
    store, _retrieval, ledger = _services(tmp_path)
    first = store.add(
        "repository sample/field-notes category research The recorded licence is Apache-2.0.",
        source="zetaris",
        origin="document",
        label="evidence",
        tags=["evidence"],
        entities=["sample/field-notes"],
        extra={"kind": "evidence", "claim_id": "CLM-LICENCE"},
        goal_run_id="run-1",
    )
    found = store.find_evidence("sample/field-notes", "CLM-LICENCE")
    assert found is not None and found.id == first.id
    store.refresh(
        first.id,
        content="repository sample/field-notes category research The recorded licence is Apache-2.0.",
        confidence=0.9,
        goal_run_id="run-2",
        refs=["EV-1"],
    )
    assert len([record for record in store.all_records() if record.extra.get("kind") == "evidence"]) == 1
    assert store.get(first.id).goal_run_id == "run-2"
    assert any(entry.action == "update" for entry in ledger.entries)


def test_two_writers_keep_both_records(tmp_path):
    path = tmp_path / "store.json"
    ledger_path = tmp_path / "ledger.jsonl"

    def make():
        return MemoryStoreService(
            ledger=TrustLedgerService(ledger_path, clock=lambda: 5),
            path=path,
            clock=lambda: 5,
        )

    first = make()
    first.hydrate()
    second = make()
    second.hydrate()
    first.add("from-a", source="s", origin="manual", label="t")
    second.add("from-b", source="s", origin="manual", label="t")
    loaded = make()
    loaded.hydrate()
    assert {record.content for record in loaded.records.values()} == {"from-a", "from-b"}
    assert "from-a" in path.read_text(encoding="utf-8")


def test_a_failed_replace_leaves_the_previous_file(tmp_path, monkeypatch):
    store, _retrieval, _ledger = _services(tmp_path)
    store.add("alpha café", source="s", origin="manual", label="t")
    original = (tmp_path / "store.json").read_bytes()
    assert "café".encode("utf-8") in original

    def boom(_src, _dst):
        raise OSError("disk")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.add("beta", source="s", origin="manual", label="t")
    assert (tmp_path / "store.json").read_bytes() == original


def test_parallel_adds_keep_both_records(tmp_path):
    path = tmp_path / "store.json"
    ledger_path = tmp_path / "ledger.jsonl"
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def worker(label: str) -> None:
        try:
            store = MemoryStoreService(
                ledger=TrustLedgerService(ledger_path, clock=lambda: 5),
                path=path,
                clock=lambda: 5,
            )
            store.hydrate()
            barrier.wait(timeout=5)
            store.add(label, source="s", origin="manual", label="t")
        except BaseException as exc:  # the test thread must report this, not die silent
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(label,)) for label in ("alpha", "beta")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()
    assert errors == []
    loaded = MemoryStoreService(
        ledger=TrustLedgerService(ledger_path, clock=lambda: 5),
        path=path,
        clock=lambda: 5,
    )
    loaded.hydrate()
    assert {record.content for record in loaded.records.values()} == {"alpha", "beta"}


def test_lock_closes_the_handle_when_locking_raises(tmp_path, monkeypatch):
    import sys
    import types

    from app.engines.hmem.store import _FileLock

    fake = types.ModuleType("msvcrt")
    fake.LK_LOCK = 0
    fake.LK_UNLCK = 1

    def locking(*_args):
        raise OSError("lock failed")

    fake.locking = locking
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr("app.engines.hmem.store.os.name", "nt")
    opened = []
    real_open = open

    def tracking(*args, **kwargs):
        handle = real_open(*args, **kwargs)
        opened.append(handle)
        return handle

    monkeypatch.setattr("builtins.open", tracking)
    lock = _FileLock(tmp_path / "gate1_verdicts.json.lock")
    with pytest.raises(OSError, match="lock failed"):
        with lock:
            pass
    assert opened and opened[0].closed
    assert lock._handle is None


def test_find_verdicts_matches_repo_id_case_insensitively(tmp_path):
    store, _retrieval, _ledger = _services(tmp_path)
    store.add(
        "owner/repo gate-1 verdict CANDIDATE",
        source="run-1",
        origin="model_response",
        label="gate1_verdict",
        layer="long_term",
        entities=["Owner/Repo"],
        extra={"kind": "verdict", "verdict": "CANDIDATE"},
    )
    assert len(store.find_verdicts("owner/repo")) == 1
    assert store.find_verdicts("other/repo") == []


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
