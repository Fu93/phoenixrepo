from evidence.factory import create_evidence
from evidence.models import Claim


def build_conflict_fixture():
    documentation = create_evidence(
        evidence_id="EV-DOC-001",
        evidence_type="documentation",
        source="README.md",
        locator="README.md:1-20",
        content="This project is an AI interview coach.",
        collector="synthetic-fixture",
        confidence=0.95,
    )
    implementation = create_evidence(
        evidence_id="EV-SRC-001",
        evidence_type="source_code",
        source="src/",
        locator="src/teleprompter/",
        content="The application displays a scrolling teleprompter and speech recognition.",
        collector="synthetic-fixture",
        confidence=0.90,
    )
    claim = Claim(
        id="CLM-001",
        statement="The primary product is an AI interview coach.",
    )
    return claim, documentation, implementation
