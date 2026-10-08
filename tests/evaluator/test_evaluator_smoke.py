import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.run import app

client = TestClient(app)


def test_evaluator_can_replay_a_sample_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    response = client.post(
        "/run",
        json={"repository": "sample://foundation", "mode": "sample", "max_repair_iterations": 3},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["decision"] is None
    assert data["foundation_only"] is True
    assert data["state"] == "EVIDENCE_COMPLETE"
    run_id = data["run_id"]
    context = json.loads((Path("run_artifacts") / run_id / "run-context.json").read_text())
    pack = json.loads((Path("run_artifacts") / run_id / "evidence-pack.json").read_text())
    events = [
        json.loads(line)["event"]
        for line in Path(data["log_file"]).read_text(encoding="utf-8").splitlines()
    ]
    assert context["run_id"] == run_id
    assert context["fingerprint"] == data["run_fingerprint"]
    assert pack["run_id"] == run_id
    assert "RUN_STARTED" in events
    assert "EVIDENCE_PACK_CREATED" in events


def test_invalid_mode_is_422():
    response = client.post("/run", json={"repository": "sample://foundation", "mode": "invalid"})
    assert response.status_code == 422


def test_health_reports_pipeline_version():
    data = client.get("/health").json()
    assert data["status"] == "ok"
    assert data["service"] == "phoenixrepo"
    assert data["pipeline_version"] == "0.1.0"
