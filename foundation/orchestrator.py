from uuid import uuid4

from audit.trail import AuditTrail
from evidence.graph import EvidenceGraphEngine
from evidence.models import Claim
from sample_data.source import SampleEvidenceSource
from state.machine import PhoenixState, StateMachine
from trace.logger import TraceLogger


class FoundationOrchestrator:
    """Stops before market judgment, GO/NO-GO, build, or validation."""

    def __init__(self) -> None:
        self.run_id = f"phoenix-{uuid4().hex[:8]}"
        self.trace = TraceLogger(self.run_id)
        self.audit = AuditTrail(self.run_id)
        self.graph = EvidenceGraphEngine(self.run_id)
        self.state = StateMachine(PhoenixState.INGESTED, audit_trail=self.audit)

    def run(self) -> dict:
        self.trace.event("RUN_STARTED", run_id=self.run_id, mode="sample")
        source = SampleEvidenceSource()
        evidence_items = source.collect()
        self.trace.event("EVIDENCE_COLLECTION_STARTED", count=len(evidence_items))
        for evidence in evidence_items:
            self.graph.add_evidence(evidence)
        self.trace.event("EVIDENCE_COLLECTION_COMPLETED", count=len(evidence_items))

        claim = Claim(
            id="CLM-FOUNDATION",
            statement="The sample repository contains repository, documentation, and source-code evidence.",
        )
        self.graph.add_claim(claim)
        for evidence in evidence_items:
            self.graph.link_support(claim.id, evidence.id)

        self.state.transition(
            PhoenixState.RECONNAISSANCE_COMPLETE,
            actor="orchestrator",
            reason="Synthetic repository reconnaissance completed.",
            evidence_ids=[evidence_items[0].id],
        )
        self.state.transition(
            PhoenixState.EVIDENCE_COMPLETE,
            actor="orchestrator",
            reason="Synthetic evidence collection completed.",
            evidence_ids=[evidence.id for evidence in evidence_items],
        )
        for event in self.audit.events:
            self.trace.audit_event(event)
        self.trace.event(
            "FOUNDATION_COMPLETED",
            final_state=self.state.current_state.value,
            evidence_count=len(self.graph.graph.evidence),
            claim_count=len(self.graph.graph.claims),
        )
        return {
            "run_id": self.run_id,
            "mode": "sample",
            "foundation_only": True,
            "status": "FOUNDATION_COMPLETE",
            "state": self.state.current_state.value,
            "evidence_count": len(self.graph.graph.evidence),
            "claims_count": len(self.graph.graph.claims),
            "audit_events": len(self.audit.events),
            "decision": None,
            "log_file": str(self.trace.path),
        }
