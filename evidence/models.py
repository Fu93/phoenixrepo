from datetime import datetime, timezone
from enum import Enum
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


class VerificationStatus(str, Enum):
    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"


class ClaimStatus(str, Enum):
    UNRESOLVED = "UNRESOLVED"
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    MIXED = "MIXED"


class EvidenceProvenance(BaseModel):
    source_type: str
    collector: str
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: str | None = None
    parent_evidence_id: str | None = None


class Evidence(BaseModel):
    id: str
    type: EvidenceType
    source: str
    locator: str | None = None
    content: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    provenance: EvidenceProvenance
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    verified_by: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Claim(BaseModel):
    id: str
    statement: str
    status: ClaimStatus = ClaimStatus.UNRESOLVED
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
