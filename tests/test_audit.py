import pytest

from audit.trail import AuditTrail
from state.machine import InvalidTransition, PhoenixState, StateMachine


def test_state_transition_creates_audit_event():
    audit = AuditTrail(run_id="audit-test-001")
    machine = StateMachine(PhoenixState.INGESTED, audit_trail=audit)
    machine.transition(
        PhoenixState.RECONNAISSANCE_COMPLETE,
        actor="orchestrator",
        reason="Initial repository reconnaissance completed.",
        evidence_ids=["EV-REPO-001"],
    )
    assert len(audit.events) == 1
    event = audit.events[0]
    assert event.actor == "orchestrator"
    assert event.from_state == "INGESTED"
    assert event.to_state == "RECONNAISSANCE_COMPLETE"
    assert event.evidence_ids == ["EV-REPO-001"]


def test_decision_without_evidence_is_rejected():
    audit = AuditTrail(run_id="audit-test-002")
    machine = StateMachine(PhoenixState.MARKET_ANALYZED, audit_trail=audit)
    with pytest.raises(InvalidTransition):
        machine.transition(
            PhoenixState.VALUE_DECISION,
            actor="intelligence-agent",
            reason="Attempted decision.",
        )
    assert len(audit.events) == 0
