from datetime import datetime, timezone

from evidence.integrity import content_hash
from evidence.models import Claim, ClaimStatus, Evidence, EvidenceGraph, EvidenceRelation, VerificationStatus
from evidence.pack import EvidencePack


class EvidenceGraphEngine:
    def __init__(self, run_id: str) -> None:
        self.graph = EvidenceGraph(run_id=run_id)

    def add_evidence(self, evidence: Evidence) -> None:
        if self.get_evidence(evidence.id):
            raise ValueError(f"Evidence already exists: {evidence.id}")
        self.graph.evidence.append(evidence)

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        return next((item for item in self.graph.evidence if item.id == evidence_id), None)

    def add_claim(self, claim: Claim) -> None:
        if self.get_claim(claim.id):
            raise ValueError(f"Claim already exists: {claim.id}")
        self.graph.claims.append(claim)

    def get_claim(self, claim_id: str) -> Claim | None:
        return next((item for item in self.graph.claims if item.id == claim_id), None)

    def link_support(self, claim_id: str, evidence_id: str) -> None:
        claim = self._require_claim(claim_id)
        self._require_evidence(evidence_id)
        if evidence_id not in claim.evidence_ids:
            claim.evidence_ids.append(evidence_id)
        self.graph.relations.append(
            EvidenceRelation(source_id=claim_id, target_id=evidence_id, relation="supports")
        )

    def link_contradiction(self, claim_id: str, evidence_id: str) -> None:
        claim = self._require_claim(claim_id)
        self._require_evidence(evidence_id)
        if evidence_id not in claim.contradiction_ids:
            claim.contradiction_ids.append(evidence_id)
        self.graph.relations.append(
            EvidenceRelation(source_id=claim_id, target_id=evidence_id, relation="contradicts")
        )

    def supporting_evidence(self, claim_id: str) -> list[Evidence]:
        claim = self._require_claim(claim_id)
        return [self._require_evidence(evidence_id) for evidence_id in claim.evidence_ids]

    def contradicting_evidence(self, claim_id: str) -> list[Evidence]:
        claim = self._require_claim(claim_id)
        return [self._require_evidence(evidence_id) for evidence_id in claim.contradiction_ids]

    def verify_evidence(self, evidence_id: str, verifier: str) -> None:
        evidence = self._require_evidence(evidence_id)
        evidence.verification_status = VerificationStatus.VERIFIED
        evidence.verified_by = verifier

    def reject_evidence(self, evidence_id: str, verifier: str) -> None:
        evidence = self._require_evidence(evidence_id)
        evidence.verification_status = VerificationStatus.REJECTED
        evidence.verified_by = verifier

    def calculate_confidence(self, claim_id: str) -> float:
        self._require_claim(claim_id)
        supporting = self.supporting_evidence(claim_id)
        if not supporting:
            return 0.0
        return round(sum(self._weight(item) for item in supporting) / len(supporting), 4)

    def verify_claim(self, claim_id: str) -> dict:
        claim = self._require_claim(claim_id)
        supporting = self.supporting_evidence(claim_id)
        contradicting = self.contradicting_evidence(claim_id)
        if not supporting and not contradicting:
            status = ClaimStatus.UNRESOLVED
            confidence = 0.0
        elif supporting and not contradicting:
            status = ClaimStatus.SUPPORTED
            confidence = self.calculate_confidence(claim_id)
        elif contradicting and not supporting:
            status = ClaimStatus.CONTRADICTED
            confidence = 0.0
        else:
            status = ClaimStatus.MIXED
            confidence = self.calculate_confidence(claim_id) * 0.5
        claim.status = status
        claim.confidence = max(0.0, min(1.0, confidence))
        return {
            "claim_id": claim.id,
            "status": claim.status.value,
            "confidence": claim.confidence,
            "supporting_evidence_ids": [item.id for item in supporting],
            "contradicting_evidence_ids": [item.id for item in contradicting],
        }

    def export(self) -> dict:
        return self.graph.model_dump(mode="json")

    def export_pack(self, run_id: str, metadata: dict | None = None) -> EvidencePack:
        graph = self.export()
        pack = EvidencePack(
            run_id=run_id,
            created_at=datetime.now(timezone.utc),
            evidence=graph["evidence"],
            claims=graph["claims"],
            relations=graph["relations"],
            metadata=metadata or {},
        )
        payload = pack.model_dump(mode="json", exclude={"integrity_hash"})
        pack.integrity_hash = content_hash(payload)
        return pack

    def _weight(self, evidence: Evidence) -> float:
        if evidence.verification_status == VerificationStatus.VERIFIED:
            return evidence.confidence
        if evidence.verification_status == VerificationStatus.UNVERIFIED:
            return evidence.confidence * 0.7
        return 0.0

    def _require_claim(self, claim_id: str) -> Claim:
        claim = self.get_claim(claim_id)
        if claim is None:
            raise ValueError(f"Unknown claim: {claim_id}")
        return claim

    def _require_evidence(self, evidence_id: str) -> Evidence:
        evidence = self.get_evidence(evidence_id)
        if evidence is None:
            raise ValueError(f"Unknown evidence: {evidence_id}")
        return evidence
