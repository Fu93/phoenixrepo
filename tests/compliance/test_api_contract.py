from fastapi.testclient import TestClient

from api.run import app

client = TestClient(app)

SAMPLE = {"repository": "sample://phoenix", "mode": "sample", "max_repair_iterations": 0}


def test_run_endpoint_exists():
    assert client.post("/run", json=SAMPLE).status_code == 200


def test_run_returns_json():
    response = client.post("/run", json=SAMPLE)
    assert response.headers["content-type"].startswith("application/json")
    assert isinstance(response.json(), dict)


def test_run_response_has_required_fields():
    data = client.post("/run", json=SAMPLE).json()
    required = {
        "run_id",
        "run_fingerprint",
        "mode",
        "foundation_only",
        "status",
        "state",
        "evidence_count",
        "claims_count",
        "audit_events",
    }
    assert required.issubset(data.keys())


def test_live_mode_is_not_available():
    response = client.post("/run", json={**SAMPLE, "mode": "live"})
    assert response.status_code == 400
