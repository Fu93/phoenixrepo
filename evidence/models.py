from typing import Any, Literal

from pydantic import BaseModel, Field

EvidenceType = Literal[
    "repository",
    "source_code",
    "documentation",
    "git_history",
    "test",
    "runtime",
    "market",
    "external",
]
RelationType = Literal["supports", "contradicts", "derived_from"]


class Evidence(BaseModel):
    id: str
    type: EvidenceType
    source: str
    locator: str | None = None
    content: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Claim(BaseModel):
    id: str
    statement: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(default_factory=list)
    contradiction_ids: list[str] = Field(default_factory=list)


class EvidenceRelation(BaseModel):
    source_id: str
    target_id: str
    relation: RelationType


class EvidenceGraph(BaseModel):
    run_id: str
    evidence: list[Evidence] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    relations: list[EvidenceRelation] = Field(default_factory=list)
