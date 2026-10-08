from evidence.factory import create_evidence
from evidence.graph import EvidenceGraphEngine
from evidence.models import Claim


def _graph() -> EvidenceGraphEngine:
    return EvidenceGraphEngine(run_id="claim-verification")


def test_supported_claim():
    graph = _graph()
    evidence = create_evidence(
        evidence_id="EV-DOC",
        evidence_type="documentation",
        source="sample://README.md",
        content="This project is an AI interview coach.",
        collector="synthetic_fixture",
    )
    graph.add_evidence(evidence)
    graph.add_claim(Claim(id="CLM-SUPPORTED", statement="The project describes itself as an AI interview coach."))
    graph.link_support("CLM-SUPPORTED", evidence.id)
    result = graph.verify_claim("CLM-SUPPORTED")
    assert result["status"] == "SUPPORTED"
    assert result["confidence"] > 0


def test_contradicted_claim():
    graph = _graph()
    evidence = create_evidence(
        evidence_id="EV-RUNTIME",
        evidence_type="runtime",
        source="sample://runtime",
        content="Observed behavior is a teleprompter workflow rather than autonomous interview coaching.",
        collector="synthetic_fixture",
    )
    graph.add_evidence(evidence)
    graph.add_claim(Claim(id="CLM-CONTRA", statement="The project behaves as an autonomous AI interview coach."))
    graph.link_contradiction("CLM-CONTRA", evidence.id)
    result = graph.verify_claim("CLM-CONTRA")
    assert result["status"] == "CONTRADICTED"
    assert result["confidence"] == 0.0


def test_mixed_claim_is_discounted():
    graph = _graph()
    documentation = create_evidence(
        evidence_id="EV-DOC",
        evidence_type="documentation",
        source="sample://README.md",
        content="This project is an AI interview coach.",
        collector="synthetic_fixture",
    )
    runtime = create_evidence(
        evidence_id="EV-RUNTIME",
        evidence_type="runtime",
        source="sample://runtime",
        content="Observed behavior is a teleprompter workflow rather than autonomous interview coaching.",
        collector="synthetic_fixture",
    )
    graph.add_evidence(documentation)
    graph.add_evidence(runtime)
    graph.add_claim(Claim(id="CLM-MIXED", statement="The primary product is an AI interview coach."))
    graph.link_support("CLM-MIXED", documentation.id)
    graph.link_contradiction("CLM-MIXED", runtime.id)
    result = graph.verify_claim("CLM-MIXED")
    assert result["status"] == "MIXED"
    assert 0.0 < result["confidence"] < 1.0


def test_unresolved_claim():
    graph = _graph()
    graph.add_claim(Claim(id="CLM-OPEN", statement="The project has strong market demand."))
    result = graph.verify_claim("CLM-OPEN")
    assert result["status"] == "UNRESOLVED"
    assert result["confidence"] == 0.0
