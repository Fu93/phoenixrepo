from foundation.orchestrator import FoundationOrchestrator


def test_foundation_pipeline_runs():
    result = FoundationOrchestrator("sample://phoenix", "sample", 0).run()
    assert result["foundation_only"] is True
    assert result["status"] == "FOUNDATION_COMPLETE"
    assert result["state"] == "EVIDENCE_COMPLETE"
    assert result["evidence_count"] == 3
    assert result["claims_count"] == 1
    assert result["audit_events"] == 3
    assert result["decision"] is None
    assert result["claim_verification"]["status"] == "SUPPORTED"
