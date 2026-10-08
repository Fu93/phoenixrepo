from typing import Any

from pydantic import BaseModel, Field


class Decision(BaseModel):
    type: str
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class RunResponse(BaseModel):
    run_id: str
    status: str
    stage: str
    confidence: float | None = None
    decision: Decision | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
