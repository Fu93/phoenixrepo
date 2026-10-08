from artifacts.store import ArtifactStore
from audit.trail import AuditTrail
from evidence.graph import EvidenceGraphEngine
from evidence.models import Claim
from run.context import RunContext
from sample_data.source import SampleEvidenceSource
from state.machine import PhoenixState, StateMachine
from trace.logger import TraceLogger


class FoundationOrchestrator:
    """Stops before market judgment, GO/NO-GO, build, or validation."""

    def __init__(self, repository: str, mode: str, max_repair_iterations: int) -> None:
        self.context = RunContext.create(repository, mode, max_repair_iterations)
        self.run_id = self.context.run_id
        self.trace = TraceLogger(self.run_id)
        self.audit = AuditTrail(self.run_id)
        self.graph = EvidenceGraphEngine(self.run_id)
        self.state = StateMachine(PhoenixState.INGESTED, audit_trail=self.audit)
        self.artifacts = ArtifactStore()

    def run(self) -> dict:
        self.trace.event(
            "RUN_STARTED",
            run_id=self.run_id,
            repository=self.context.repository,
            mode=self.context.mode,
            max_repair_iterations=self.context.max_repair_iterations,
            pipeline_version=self.context.pipeline_version,
            foundation_only=self.context.foundation_only,
        )
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
        verification = self.graph.verify_claim(claim.id)
        self.audit.record(
            actor="evidence_engine",
            event_type="CLAIM_VERIFIED",
            reason=f"Claim verification completed: {verification['status']}",
            evidence_ids=verification["supporting_evidence_ids"] + verification["contradicting_evidence_ids"],
            metadata=verification,
        )
        self.trace.event(
            "CLAIM_VERIFIED",
            claim_id=claim.id,
            status=verification["status"],
            confidence=verification["confidence"],
        )

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
        pack = self.graph.export_pack(
            run_id=self.run_id,
            metadata={
                "repository": self.context.repository,
                "mode": self.context.mode,
                "foundation_only": self.context.foundation_only,
                "pipeline_version": self.context.pipeline_version,
                "max_repair_iterations": self.context.max_repair_iterations,
            },
        )
        artifact_path = self.artifacts.save_evidence_pack(pack)
        context_path = self.artifacts.save_run_context(self.context)
        self.trace.event(
            "EVIDENCE_PACK_CREATED",
            path=str(artifact_path),
            integrity_hash=pack.integrity_hash,
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
            "run_fingerprint": self.context.fingerprint,
            "mode": self.context.mode,
            "foundation_only": True,
            "status": "FOUNDATION_COMPLETE",
            "state": self.state.current_state.value,
            "evidence_count": len(self.graph.graph.evidence),
            "claims_count": len(self.graph.graph.claims),
            "audit_events": len(self.audit.events),
            "decision": None,
            "claim_verification": verification,
            "evidence_pack": {
                "path": str(artifact_path),
                "context_path": str(context_path),
                "integrity_hash": pack.integrity_hash,
            },
            "log_file": str(self.trace.path),
        }
