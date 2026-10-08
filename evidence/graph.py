from evidence.models import Claim, Evidence, EvidenceGraph, EvidenceRelation


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

    def calculate_confidence(self, claim_id: str) -> float:
        self._require_claim(claim_id)
        supporting = self.supporting_evidence(claim_id)
        contradicting = self.contradicting_evidence(claim_id)
        if not supporting:
            return 0.0
        support_score = sum(item.confidence for item in supporting) / len(supporting)
        if not contradicting:
            return round(support_score, 4)
        contradiction_score = sum(item.confidence for item in contradicting) / len(contradicting)
        confidence = support_score * (1 - contradiction_score)
        return round(max(0.0, min(1.0, confidence)), 4)

    def export_json(self) -> dict:
        return self.graph.model_dump()

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
