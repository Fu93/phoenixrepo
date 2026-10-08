import hashlib

from evidence.models import Evidence, EvidenceProvenance


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def create_evidence(
    *,
    evidence_id: str,
    evidence_type: str,
    source: str,
    content: str,
    collector: str,
    locator: str | None = None,
    confidence: float = 1.0,
    parent_evidence_id: str | None = None,
) -> Evidence:
    return Evidence(
        id=evidence_id,
        type=evidence_type,
        source=source,
        locator=locator,
        content=content,
        confidence=confidence,
        provenance=EvidenceProvenance(
            source_type=evidence_type,
            collector=collector,
            content_hash=content_hash(content),
            parent_evidence_id=parent_evidence_id,
        ),
    )
