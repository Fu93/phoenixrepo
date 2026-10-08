from audit.trail import AuditTrail


def test_audit_event_round_trip():
    trail = AuditTrail("phoenix-schema")
    event = trail.record(
        actor="orchestrator",
        event_type="STATE_TRANSITION",
        from_state="INGESTED",
        to_state="RECONNAISSANCE_COMPLETE",
        reason="Synthetic.",
        evidence_ids=["EV-1"],
    )
    restored = event.model_validate(event.model_dump(mode="json"))
    assert restored.event_id == "AUD-0001"
    assert restored.evidence_ids == ["EV-1"]
