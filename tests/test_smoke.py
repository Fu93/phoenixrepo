import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.run import app

client = TestClient(app)


def test_run_endpoint():
    response = client.post(
        "/run",
        json={"repository": "sample/phoenix-ledger", "mode": "sample"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "COMPLETE"
    assert data["state"] == "CANDIDATE"
    assert data["decision"] == "CANDIDATE"
    assert data["foundation_only"] is False
    text = Path(data["log_file"]).read_text(encoding="utf-8")
    events = [json.loads(line)["event"] for line in text.splitlines()]
    assert "RUN_STARTED" in events
    assert "VERDICT" in events
    assert "CRITIQUE_REJECT" in events
    assert "HMEM_RECALL" in events
