from typing import Any

from pydantic import BaseModel, Field


class Decision(BaseModel):
    type: str
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class RunResponse(BaseModel):
    run_id: str
    run_fingerprint: str
    mode: str
    foundation_only: bool
    status: str
    state: str
    evidence_count: int
    claims_count: int
    audit_events: int
    decision: str | None = None
    claim_verification: dict[str, Any] | None = None
    evidence_pack: dict[str, Any] | None = None
    log_file: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
