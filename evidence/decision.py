from enum import Enum

from pydantic import BaseModel, Field


class DecisionType(str, Enum):
    GO = "GO"
    NO_GO = "NO_GO"


class ValueDecision(BaseModel):
    decision: DecisionType
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    evidence_ids: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
