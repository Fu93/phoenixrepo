from evidence.graph import EvidenceGraphEngine
from evidence.pack import EvidencePack


def test_saved_pack_reloads():
    graph = EvidenceGraphEngine("replay-run")
    pack = graph.export_pack("replay-run", metadata={"foundation_only": True})
    restored = EvidencePack.model_validate(pack.model_dump(mode="json"))
    assert restored.run_id == "replay-run"
    assert restored.metadata["foundation_only"] is True
