from fastapi.testclient import TestClient

from api.run import app

client = TestClient(app)


def run_sample() -> dict:
    response = client.post(
        "/run",
        json={"repository": "sample://phoenix", "mode": "sample", "max_repair_iterations": 0},
    )
    assert response.status_code == 200
    return response.json()


def test_sample_mode_runs_real_pipeline():
    result = run_sample()
    assert result["foundation_only"] is True
    assert result["evidence_count"] > 0
    assert result["claims_count"] > 0
    assert result["audit_events"] > 0
    assert result["state"] == "EVIDENCE_COMPLETE"


def test_sample_mode_does_not_return_final_decision():
    assert run_sample()["decision"] is None
