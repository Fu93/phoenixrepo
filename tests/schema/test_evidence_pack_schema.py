from evidence.graph import EvidenceGraphEngine
from evidence.integrity import verify_integrity
from evidence.pack import EvidencePack
from config.versions import SCHEMA_VERSION


def test_pack_round_trip_and_hash():
    graph = EvidenceGraphEngine("schema-run")
    pack = graph.export_pack("schema-run", metadata={"mode": "sample"})
    restored = EvidencePack.model_validate(pack.model_dump(mode="json"))
    assert restored.schema_version == SCHEMA_VERSION
    assert verify_integrity(restored) is True
    again = graph.export_pack("schema-run", metadata={"mode": "sample"})
    assert again.integrity_hash == pack.integrity_hash
