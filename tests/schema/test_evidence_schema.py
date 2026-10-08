import pytest
from pydantic import ValidationError

from evidence.factory import create_evidence


def test_evidence_round_trip():
    evidence = create_evidence(
        evidence_id="EV-1",
        evidence_type="documentation",
        source="sample://README.md",
        content="Synthetic README.",
        collector="schema-test",
    )
    restored = evidence.model_validate(evidence.model_dump(mode="json"))
    assert restored.id == "EV-1"
    assert restored.provenance.content_hash
    assert restored.verification_status.value == "UNVERIFIED"


def test_evidence_rejects_unknown_type():
    with pytest.raises(ValidationError):
        create_evidence(
            evidence_id="EV-2",
            evidence_type="opinion",
            source="sample://x",
            content="no",
            collector="schema-test",
        )
