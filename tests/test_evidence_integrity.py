from evidence.factory import create_evidence
from evidence.graph import EvidenceGraphEngine


def test_same_graph_has_stable_content_hash():
    graph = EvidenceGraphEngine(run_id="same-run")
    graph.add_evidence(
        create_evidence(
            evidence_id="EV-STABLE",
            evidence_type="repository",
            source="sample://repo",
            content="Synthetic repository",
            collector="test",
        )
    )
    pack_a = graph.export_pack(run_id="same-run", metadata={"mode": "sample"})
    pack_b = graph.export_pack(run_id="same-run", metadata={"mode": "sample"})
    assert pack_a.integrity_hash == pack_b.integrity_hash
    assert pack_a.created_at != pack_b.created_at or pack_a.integrity_hash == pack_b.integrity_hash
