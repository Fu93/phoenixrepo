import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.run import app
from evidence.integrity import verify_integrity
from evidence.pack import EvidencePack

client = TestClient(app)
SAMPLE = {"repository": "sample://phoenix", "mode": "sample", "max_repair_iterations": 0}


def _run(tmp_path, monkeypatch) -> dict:
    monkeypatch.chdir(tmp_path)
    return client.post("/run", json=SAMPLE).json()


def test_evidence_pack_created(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch)
    run_id = result["run_id"]
    assert (Path("run_artifacts") / run_id / "evidence-pack.json").exists()
    assert (Path("run_artifacts") / run_id / "run-context.json").exists()


def test_artifacts_match_run(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch)
    run_id = result["run_id"]
    context = json.loads((Path("run_artifacts") / run_id / "run-context.json").read_text())
    pack = json.loads((Path("run_artifacts") / run_id / "evidence-pack.json").read_text())
    assert context["run_id"] == run_id
    assert pack["run_id"] == run_id


def test_saved_evidence_pack_integrity(tmp_path, monkeypatch):
    result = _run(tmp_path, monkeypatch)
    path = Path("run_artifacts") / result["run_id"] / "evidence-pack.json"
    pack = EvidencePack.model_validate(json.loads(path.read_text(encoding="utf-8")))
    assert verify_integrity(pack) is True
