from audit.trail import AuditTrail
from evidence.factory import create_evidence
from evidence.graph import EvidenceGraphEngine
from state.machine import PhoenixState, StateMachine
from trace.logger import TraceLogger


def test_evidence_state_audit_integration():
    run_id = "foundation-001"
    evidence_graph = EvidenceGraphEngine(run_id=run_id)
    evidence = create_evidence(
        evidence_id="EV-001",
        evidence_type="repository",
        source="synthetic",
        content="Synthetic repository exists.",
        collector="test",
    )
    evidence_graph.add_evidence(evidence)
    audit = AuditTrail(run_id=run_id)
    machine = StateMachine(PhoenixState.INGESTED, audit_trail=audit)
    machine.transition(
        PhoenixState.RECONNAISSANCE_COMPLETE,
        actor="orchestrator",
        reason="Repository existence confirmed.",
        evidence_ids=["EV-001"],
    )
    trace = TraceLogger(run_id)
    trace.audit_event(audit.events[0])
    assert machine.current_state == PhoenixState.RECONNAISSANCE_COMPLETE
    assert len(evidence_graph.graph.evidence) == 1
    assert len(audit.events) == 1
    assert "EV-001" in audit.events[0].evidence_ids
    assert "AUDIT" in trace.path.read_text(encoding="utf-8")
