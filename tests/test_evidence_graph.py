from evidence.graph import EvidenceGraphEngine
from tests.fixtures.evidence_conflict import build_conflict_fixture


def test_add_and_link_evidence():
    claim, documentation, implementation = build_conflict_fixture()
    graph = EvidenceGraphEngine(run_id="test-run-001")
    graph.add_claim(claim)
    graph.add_evidence(documentation)
    graph.add_evidence(implementation)
    graph.link_support("CLM-001", "EV-DOC-001")
    graph.link_contradiction("CLM-001", "EV-SRC-001")
    assert len(graph.supporting_evidence("CLM-001")) == 1
    assert len(graph.contradicting_evidence("CLM-001")) == 1


def test_confidence_accounts_for_contradiction():
    claim, documentation, implementation = build_conflict_fixture()
    graph = EvidenceGraphEngine(run_id="test-run-002")
    graph.add_claim(claim)
    graph.add_evidence(documentation)
    graph.add_evidence(implementation)
    graph.link_support("CLM-001", "EV-DOC-001")
    graph.link_contradiction("CLM-001", "EV-SRC-001")
    confidence = graph.calculate_confidence("CLM-001")
    assert confidence < 0.95
    assert confidence >= 0.0


def test_claim_without_evidence_has_zero_confidence():
    claim, _, _ = build_conflict_fixture()
    graph = EvidenceGraphEngine(run_id="test-run-003")
    graph.add_claim(claim)
    assert graph.calculate_confidence("CLM-001") == 0.0


def test_evidence_has_provenance():
    _, documentation, _ = build_conflict_fixture()
    assert documentation.provenance.collector == "synthetic-fixture"
    assert documentation.provenance.content_hash is not None
    assert documentation.provenance.retrieved_at is not None


def test_evidence_can_be_verified():
    _, documentation, _ = build_conflict_fixture()
    graph = EvidenceGraphEngine(run_id="test-verification")
    graph.add_evidence(documentation)
    graph.verify_evidence("EV-DOC-001", verifier="test-validator")
    evidence = graph.get_evidence("EV-DOC-001")
    assert evidence is not None
    assert evidence.verification_status.value == "VERIFIED"
    assert evidence.verified_by == "test-validator"


def test_graph_export():
    claim, documentation, _ = build_conflict_fixture()
    graph = EvidenceGraphEngine(run_id="export-test")
    graph.add_claim(claim)
    graph.add_evidence(documentation)
    graph.link_support("CLM-001", "EV-DOC-001")
    exported = graph.export()
    assert exported["run_id"] == "export-test"
    assert len(exported["claims"]) == 1
    assert len(exported["evidence"]) == 1
    assert len(exported["relations"]) == 1
