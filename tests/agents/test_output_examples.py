import json
from pathlib import Path

from app.investigation import InvestigationOrchestrator
from config.settings import Settings

CASES = (
    ("example_1.json", "sample/phoenix-ledger", 0, "CANDIDATE"),
    ("example_2.json", "sample/generic-qr", 0, "NO_GO"),
    ("example_3.json", "sample/field-notes", 1, "RESURRECTION_FAILED"),
)


def _statements(payload: dict) -> list[str]:
    return [
        claim["statement"]
        for claim in payload["metadata"]["claims"]
        if not claim["claim_id"].startswith("CLM-PRIOR")
    ]


def test_committed_outputs_match_fresh_sample_runs(tmp_path):
    for filename, repo, repairs, decision in CASES:
        saved = json.loads((Path("output_examples") / filename).read_text(encoding="utf-8"))
        assert saved["mode"] == "sample"
        assert saved["decision"] == decision
        assert saved["metadata"]["llm"]["sample_standin"] is True
        assert "deterministic stand-in" in saved["metadata"]["llm"]["label"]
        settings = Settings(
            sample_mode=True,
            hmem_store_path=str(tmp_path / filename),
            log_dir=str(tmp_path / "logs"),
            _env_file=None,
        )
        fresh = InvestigationOrchestrator(
            repo,
            "sample",
            repairs,
            settings,
            hmem_path=tmp_path / filename,
        ).run()
        assert _statements(saved) == _statements(fresh)
        assert saved["metadata"]["scope"] == fresh["metadata"]["scope"]
