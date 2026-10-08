import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.run import app

client = TestClient(app)


def test_clean_run_only_creates_its_own_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = client.post(
        "/run",
        json={"repository": "sample://foundation", "mode": "sample", "max_repair_iterations": 3},
    ).json()
    second = client.post(
        "/run",
        json={"repository": "sample://foundation", "mode": "sample", "max_repair_iterations": 5},
    ).json()
    assert first["run_id"] != second["run_id"]
    assert first["run_fingerprint"] != second["run_fingerprint"]
    assert first["state"] == "EVIDENCE_COMPLETE"
    assert second["decision"] is None
    pack = json.loads((Path("run_artifacts") / second["run_id"] / "evidence-pack.json").read_text())
    assert pack["run_id"] == second["run_id"]
    assert "RUN_STARTED" in Path(second["log_file"]).read_text(encoding="utf-8")


def test_live_request_is_rejected_without_credentials():
    response = client.post(
        "/run",
        json={"repository": "sample://foundation", "mode": "live", "max_repair_iterations": 3},
    )
    assert response.status_code == 400
