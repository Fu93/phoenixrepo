from sample_data.source import SampleEvidenceSource


def test_sample_source_produces_evidence():
    evidence = SampleEvidenceSource().collect()
    assert len(evidence) == 3
    assert all(item.id for item in evidence)
    assert all(item.content for item in evidence)
    sources = {item.source for item in evidence}
    assert "sample://repository" in sources
    assert "sample://README.md" in sources
    assert "sample://src/" in sources
