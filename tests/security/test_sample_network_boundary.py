import socket

from fastapi.testclient import TestClient

from api.run import app

client = TestClient(app)


def test_sample_run_makes_no_network(monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError("External network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    response = client.post(
        "/run",
        json={"repository": "sample/generic-qr", "mode": "sample", "max_repair_iterations": 0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "NO_GO"
    assert body["metadata"]["llm"]["sample_standin"] is True
    assert body["metadata"]["zetaris"]["mode"] == "sample-cache"
