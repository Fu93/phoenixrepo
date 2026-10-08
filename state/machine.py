from enum import Enum

from pydantic import BaseModel, Field

from audit.trail import AuditTrail


class PhoenixState(str, Enum):
    INGESTED = "INGESTED"
    RECONNAISSANCE_COMPLETE = "RECONNAISSANCE_COMPLETE"
    EVIDENCE_COMPLETE = "EVIDENCE_COMPLETE"
    INTENT_RECONSTRUCTED = "INTENT_RECONSTRUCTED"
    MARKET_ANALYZED = "MARKET_ANALYZED"
    VALUE_DECISION = "VALUE_DECISION"
    PRODUCT_BLUEPRINT_COMPLETE = "PRODUCT_BLUEPRINT_COMPLETE"
    ENGINEERING_RESEARCH_COMPLETE = "ENGINEERING_RESEARCH_COMPLETE"
    IMPLEMENTATION_CONTRACT_READY = "IMPLEMENTATION_CONTRACT_READY"
    BUILDING = "BUILDING"
    VALIDATING = "VALIDATING"
    DIAGNOSING = "DIAGNOSING"
    REPAIRING = "REPAIRING"
    RESURRECTED = "RESURRECTED"
    NO_GO = "NO_GO"
    RESURRECTION_FAILED = "RESURRECTION_FAILED"


ALLOWED_TRANSITIONS = {
    PhoenixState.INGESTED: {PhoenixState.RECONNAISSANCE_COMPLETE},
    PhoenixState.RECONNAISSANCE_COMPLETE: {PhoenixState.EVIDENCE_COMPLETE},
    PhoenixState.EVIDENCE_COMPLETE: {PhoenixState.INTENT_RECONSTRUCTED},
    PhoenixState.INTENT_RECONSTRUCTED: {PhoenixState.MARKET_ANALYZED},
    PhoenixState.MARKET_ANALYZED: {PhoenixState.VALUE_DECISION},
    PhoenixState.VALUE_DECISION: {
        PhoenixState.PRODUCT_BLUEPRINT_COMPLETE,
        PhoenixState.NO_GO,
    },
    PhoenixState.PRODUCT_BLUEPRINT_COMPLETE: {PhoenixState.ENGINEERING_RESEARCH_COMPLETE},
    PhoenixState.ENGINEERING_RESEARCH_COMPLETE: {PhoenixState.IMPLEMENTATION_CONTRACT_READY},
    PhoenixState.IMPLEMENTATION_CONTRACT_READY: {PhoenixState.BUILDING},
    PhoenixState.BUILDING: {PhoenixState.VALIDATING},
    PhoenixState.VALIDATING: {
        PhoenixState.RESURRECTED,
        PhoenixState.DIAGNOSING,
        PhoenixState.RESURRECTION_FAILED,
    },
    PhoenixState.DIAGNOSING: {PhoenixState.REPAIRING},
    PhoenixState.REPAIRING: {
        PhoenixState.VALIDATING,
        PhoenixState.RESURRECTION_FAILED,
    },
}


class InvalidTransition(Exception):
    pass


class StateMachine:
    def __init__(self, initial_state: PhoenixState, audit_trail: AuditTrail | None = None) -> None:
        self.current_state = initial_state
        self.audit_trail = audit_trail

    def can_transition(self, next_state: PhoenixState) -> bool:
        return next_state in ALLOWED_TRANSITIONS.get(self.current_state, set())

    def transition(
        self,
        next_state: PhoenixState,
        *,
        actor: str = "orchestrator",
        reason: str | None = None,
        evidence_ids: list[str] | None = None,
    ) -> None:
        if not self.can_transition(next_state):
            raise InvalidTransition(
                f"Invalid transition: {self.current_state.value} -> {next_state.value}"
            )
        if next_state == PhoenixState.VALUE_DECISION and not evidence_ids:
            raise InvalidTransition("VALUE_DECISION requires evidence.")
        previous_state = self.current_state
        self.current_state = next_state
        if self.audit_trail:
            self.audit_trail.record(
                actor=actor,
                event_type="STATE_TRANSITION",
                from_state=previous_state.value,
                to_state=next_state.value,
                reason=reason,
                evidence_ids=evidence_ids,
            )


class RunState(BaseModel):
    state: PhoenixState
    evidence_ids: list[str] = Field(default_factory=list)
