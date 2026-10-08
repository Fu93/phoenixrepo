from evidence.models import Claim, Evidence


def build_conflict_fixture():
    documentation = Evidence(
        id="EV-DOC-001",
        type="documentation",
        source="README.md",
        locator="README.md:1-20",
        content="This project is an AI interview coach.",
        confidence=0.95,
    )
    implementation = Evidence(
        id="EV-SRC-001",
        type="source_code",
        source="src/",
        locator="src/teleprompter/",
        content="The application displays a scrolling teleprompter and speech recognition.",
        confidence=0.90,
    )
    claim = Claim(
        id="CLM-001",
        statement="The primary product is an AI interview coach.",
    )
    return claim, documentation, implementation
