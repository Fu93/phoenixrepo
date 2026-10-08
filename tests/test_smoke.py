from pathlib import Path

from fastapi.testclient import TestClient

from api.run import app

client = TestClient(app)


def test_run_endpoint():
    response = client.post(
        "/run",
        json={"repository": "https://github.com/example/repo", "mode": "sample"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "RUNNING"
    assert data["stage"] == "INGESTED"
    assert data["run_id"].startswith("phoenix-")
    log = Path(data["metadata"]["log_file"])
    assert log.exists()
    text = log.read_text(encoding="utf-8")
    assert "RUN_STARTED" in text
    assert "STATE_ENTERED" in text
