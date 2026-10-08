import socket

import pytest
from fastapi.testclient import TestClient

from api.run import app

client = TestClient(app)


def test_sample_run_makes_no_network(monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError("External network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    response = client.post(
        "/run",
        json={"repository": "sample://foundation", "mode": "sample", "max_repair_iterations": 0},
    )
    assert response.status_code == 200
    assert response.json()["decision"] is None
