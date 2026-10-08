from audit.events import AuditEvent


class AuditTrail:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self.events: list[AuditEvent] = []

    def record(
        self,
        *,
        actor: str,
        event_type: str,
        reason: str | None = None,
        from_state: str | None = None,
        to_state: str | None = None,
        evidence_ids: list[str] | None = None,
        metadata: dict | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_id=f"AUD-{len(self.events) + 1:04d}",
            run_id=self.run_id,
            actor=actor,
            event_type=event_type,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            evidence_ids=evidence_ids or [],
            metadata=metadata or {},
        )
        self.events.append(event)
        return event

    def export(self) -> list[dict]:
        return [event.model_dump(mode="json") for event in self.events]
