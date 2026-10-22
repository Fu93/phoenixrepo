import json
from pathlib import Path

from app.investigation import InvestigationOrchestrator
from app.trace_log import FIELDS
from config.settings import Settings


def _run(repo: str, repairs: int, tmp_path: Path) -> dict:
    settings = Settings(
        sample_mode=True,
        hmem_store_path=str(tmp_path / "hmem.json"),
        log_dir=str(tmp_path / "logs"),
    )
    return InvestigationOrchestrator(
        repo,
        "sample",
        repairs,
        settings,
        hmem_path=tmp_path / "hmem.json",
    ).run()


def test_three_catalog_decisions(tmp_path):
    resurrected = _run("sample/phoenix-ledger", 0, tmp_path / "a")
    no_go = _run("sample/generic-qr", 0, tmp_path / "b")
    failed = _run("sample/field-notes", 1, tmp_path / "c")
    assert resurrected["decision"] == "RESURRECTED"
    assert no_go["decision"] == "NO_GO"
    assert no_go["state"] == "NO_GO"
    assert failed["decision"] == "RESURRECTION_FAILED"
    assert failed["state"] == "RESURRECTION_FAILED"
    for result in (resurrected, no_go, failed):
        assert result["metadata"]["llm"]["sample_standin"] is True
        assert result["metadata"]["hmem"]["recalled"]
        actions = [
            json.loads(line)["action"]
            for line in Path(result["log_file"]).read_text(encoding="utf-8").splitlines()
        ]
        assert "CRITIQUE_REJECT" in actions
        assert "RETRY" in actions
        record = json.loads(Path(result["log_file"]).read_text(encoding="utf-8").splitlines()[0])
        assert set(FIELDS).issubset(record)


def test_hmem_recalls_a_prior_verdict_on_the_next_run(tmp_path):
    first = _run("sample/field-notes", 0, tmp_path)
    second = _run("sample/field-notes", 0, tmp_path)
    priors = second["metadata"]["hmem"]["prior_verdicts"]
    assert priors
    assert priors[0]["run_id"] == first["run_id"]
    assert priors[0]["decision"] == "RESURRECTION_FAILED"
    prior_claims = [claim for claim in second["metadata"]["claims"] if claim["claim_id"].startswith("CLM-PRIOR")]
    assert prior_claims
    assert prior_claims[0]["evidence"][0]["source"] == "hmem"


def test_missing_zetaris_does_not_fall_back(tmp_path):
    settings = Settings(sample_mode=False, zetaris_api_key=None, llm_provider="meterless")
    try:
        InvestigationOrchestrator("sample/phoenix-ledger", "live", 0, settings).run()
    except Exception as exc:
        assert "ZETARIS" in str(exc).upper() or "Zetaris" in str(exc)
    else:
        raise AssertionError("live mode ran without Zetaris")


def test_output_examples_are_not_an_input(tmp_path):
    text = Path("app/investigation.py").read_text(encoding="utf-8")
    assert "output_examples" not in text
    assert "api.github.com" not in text
    assert "api.x.ai" not in text
