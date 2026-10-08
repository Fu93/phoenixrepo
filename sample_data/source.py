from evidence.factory import create_evidence


class SampleEvidenceSource:
    """Synthetic evidence only. Does not inspect a repository or decide GO."""

    def collect(self) -> list:
        return [
            create_evidence(
                evidence_id="EV-SAMPLE-REPO",
                evidence_type="repository",
                source="sample://repository",
                content="Synthetic repository exists for foundation pipeline testing.",
                collector="sample_evidence_source",
            ),
            create_evidence(
                evidence_id="EV-SAMPLE-DOC",
                evidence_type="documentation",
                source="sample://README.md",
                content="Synthetic README exists for foundation pipeline testing.",
                collector="sample_evidence_source",
            ),
            create_evidence(
                evidence_id="EV-SAMPLE-SRC",
                evidence_type="source_code",
                source="sample://src/",
                content="Synthetic source directory exists for foundation pipeline testing.",
                collector="sample_evidence_source",
            ),
        ]
