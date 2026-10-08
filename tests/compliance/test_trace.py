import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.run import app

client = TestClient(app)
SAMPLE = {"repository": "sample://phoenix", "mode": "sample", "max_repair_iterations": 0}


def _trace_path(tmp_path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    data = client.post("/run", json=SAMPLE).json()
    return Path(data["log_file"])


def test_trace_file_created(tmp_path, monkeypatch):
    assert _trace_path(tmp_path, monkeypatch).exists()


def test_trace_is_valid_jsonl(tmp_path, monkeypatch):
    lines = _trace_path(tmp_path, monkeypatch).read_text(encoding="utf-8").splitlines()
    assert lines
    for line in lines:
        event = json.loads(line)
        assert "event" in event


def test_trace_contains_core_events(tmp_path, monkeypatch):
    events = [
        json.loads(line)["event"]
        for line in _trace_path(tmp_path, monkeypatch).read_text(encoding="utf-8").splitlines()
    ]
    assert "RUN_STARTED" in events
    assert "EVIDENCE_COLLECTION_STARTED" in events
    assert "EVIDENCE_COLLECTION_COMPLETED" in events
    assert "CLAIM_VERIFIED" in events
    assert "EVIDENCE_PACK_CREATED" in events
