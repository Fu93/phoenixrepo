from fastapi.testclient import TestClient

from api.run import app

client = TestClient(app)


def run_sample() -> dict:
    response = client.post(
        "/run",
        json={"repository": "sample/phoenix-ledger", "mode": "sample", "max_repair_iterations": 0},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_sample_mode_runs_the_investigation():
    result = run_sample()
    assert result["foundation_only"] is False
    assert result["evidence_count"] > 0
    assert result["claims_count"] > 0
    assert result["audit_events"] > 0
    assert result["decision"] == "RESURRECTED"
    assert result["state"] == "RESURRECTED"
    assert result["metadata"]["llm"]["sample_standin"] is True


def test_sample_mode_links_every_verdict_claim_to_zetaris():
    result = run_sample()
    assert result["metadata"]["zetaris"]["queries"]
    for claim in result["metadata"]["claims"]:
        if claim["claim_id"].startswith("CLM-PRIOR"):
            continue
        assert claim["evidence"]
        for link in claim["evidence"]:
            if link["source"] == "hmem":
                continue
            assert link["query_id"]
            assert "phoenix." in link["sql"]
            assert link["source"].startswith("phoenix.")
