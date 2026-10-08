from config.versions import PIPELINE_VERSION, SCHEMA_VERSION
from evidence.pack import EvidencePack
from run.context import RunContext


def test_versions_are_shared():
    assert SCHEMA_VERSION == "1.0"
    assert PIPELINE_VERSION == "0.1.0"
    assert EvidencePack(run_id="x", created_at="2026-10-08T00:00:00Z").schema_version == SCHEMA_VERSION
    assert RunContext.create("sample://x", "sample", 0).pipeline_version == PIPELINE_VERSION
