from evidence.factory import create_evidence
from evidence.graph import EvidenceGraphEngine
from evidence.integrity import verify_integrity
from evidence.models import Claim


def _graph_with_evidence(content: str = "Synthetic repository") -> EvidenceGraphEngine:
    graph = EvidenceGraphEngine(run_id="pack-test")
    graph.add_evidence(
        create_evidence(
            evidence_id="EV-PACK",
            evidence_type="repository",
            source="sample://repo",
            content=content,
            collector="test",
        )
    )
    return graph


def test_evidence_pack_export():
    graph = _graph_with_evidence()
    graph.add_claim(Claim(id="CLM-PACK", statement="Repository exists."))
    graph.link_support("CLM-PACK", "EV-PACK")
    pack = graph.export_pack(run_id="test-run")
    assert pack.run_id == "test-run"
    assert len(pack.evidence) == 1
    assert len(pack.claims) == 1
    assert pack.integrity_hash


def test_evidence_pack_integrity():
    pack = _graph_with_evidence().export_pack(run_id="integrity-test")
    assert verify_integrity(pack) is True


def test_evidence_pack_detects_tampering():
    pack = _graph_with_evidence(content="Original evidence").export_pack(run_id="tamper-test")
    assert verify_integrity(pack) is True
    pack.evidence[0]["content"] = "Tampered evidence"
    assert verify_integrity(pack) is False
