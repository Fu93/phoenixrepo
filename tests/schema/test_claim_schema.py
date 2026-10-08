import pytest
from pydantic import ValidationError

from evidence.models import Claim, ClaimStatus


def test_claim_defaults_to_unresolved():
    claim = Claim(id="CLM-1", statement="A sample claim.")
    restored = Claim.model_validate(claim.model_dump())
    assert restored.status == ClaimStatus.UNRESOLVED
    assert restored.confidence == 0.0


def test_claim_rejects_unknown_status():
    with pytest.raises(ValidationError):
        Claim.model_validate({"id": "CLM-1", "statement": "x", "status": "GO"})
