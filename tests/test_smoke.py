from pathlib import Path

from fastapi.testclient import TestClient

from api.run import app

client = TestClient(app)


def test_run_endpoint():
    response = client.post(
        "/run",
        json={"repository": "sample://phoenix", "mode": "sample"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "FOUNDATION_COMPLETE"
    assert data["state"] == "EVIDENCE_COMPLETE"
    assert data["decision"] is None
    assert data["foundation_only"] is True
    text = Path(data["log_file"]).read_text(encoding="utf-8")
    assert "RUN_STARTED" in text
    assert "FOUNDATION_COMPLETED" in text
