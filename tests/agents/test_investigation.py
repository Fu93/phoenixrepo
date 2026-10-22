import json
from pathlib import Path

from app.engines.hmem.ledger import TrustLedgerService
from app.engines.hmem.store import MemoryStoreService
from app.investigation import InvestigationOrchestrator
from app.trace_log import FIELDS
from config.settings import Settings


def _run(repo: str, repairs: int, tmp_path: Path) -> dict:
    settings = Settings(
        sample_mode=True,
        hmem_store_path=str(tmp_path / "hmem.json"),
        log_dir=str(tmp_path / "logs"),
        _env_file=None,
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
    assert resurrected["decision"] == "CANDIDATE"
    assert resurrected["state"] == "CANDIDATE"
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


def test_prior_verdict_survives_twelve_repeat_runs(tmp_path):
    runs = [_run("sample/field-notes", 0, tmp_path) for _ in range(12)]
    for index in range(1, len(runs)):
        priors = runs[index]["metadata"]["hmem"]["prior_verdicts"]
        assert priors
        assert priors[0]["run_id"] == runs[index - 1]["run_id"]
        assert priors[0]["decision"] == "RESURRECTION_FAILED"
    store = json.loads((tmp_path / "hmem.json").read_text(encoding="utf-8"))
    evidence = [
        record
        for record in store["records"]
        if record["extra"].get("kind") == "evidence" and "sample/field-notes" in record["entities"]
    ]
    verdicts = [
        record
        for record in store["records"]
        if record["extra"].get("kind") == "verdict" and "sample/field-notes" in record["entities"]
    ]
    assert len(evidence) == 3
    assert len({record["extra"]["claim_id"] for record in evidence}) == 3
    assert len(verdicts) == 12
    assert sum(1 for record in verdicts if not record["superseded_by"]) == 1


def test_preexisting_duplicate_evidence_collapses_on_the_next_run(tmp_path):
    path = tmp_path / "hmem.json"
    store = MemoryStoreService(
        ledger=TrustLedgerService(path.with_suffix(".ledger.jsonl"), clock=lambda: 0),
        path=path,
        clock=lambda: 0,
    )
    for _index in range(8):
        store.add(
            "repository sample/field-notes category research The recorded licence is Apache-2.0.",
            source="zetaris:phoenix.repo_evidence",
            origin="document",
            label="evidence claim",
            tags=["evidence", "research"],
            entities=["sample/field-notes"],
            extra={"kind": "evidence", "claim_id": "CLM-LICENCE"},
            goal_run_id=f"old-{_index}",
        )
    _run("sample/field-notes", 0, tmp_path)
    records = json.loads(path.read_text(encoding="utf-8"))["records"]
    current = [
        record
        for record in records
        if record["extra"].get("claim_id") == "CLM-LICENCE" and not record["superseded_by"]
    ]
    assert len(current) == 1
    assert sum(1 for record in records if record["extra"].get("claim_id") == "CLM-LICENCE") == 8


def test_sample_trace_records_requested_and_served_model(tmp_path):
    result = _run("sample/generic-qr", 0, tmp_path)
    assert result["metadata"]["llm"]["requested_model"] == "deterministic-rule-standin"
    assert result["metadata"]["llm"]["served_model"] == "deterministic-rule-standin"
    records = [
        json.loads(line)
        for line in Path(result["log_file"]).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    outputs = [
        json.loads(record["input_summary"])
        for record in records
        if record["action"] == "MODEL_OUTPUT"
    ]
    assert outputs
    assert outputs[0]["requested_model"] == "deterministic-rule-standin"
    assert outputs[0]["served_model"] == "deterministic-rule-standin"
    assert records
    model_lines = [record for record in records if record["action"] == "MODEL_OUTPUT"]
    assert model_lines[0]["requested_model"] == "deterministic-rule-standin"
    assert model_lines[0]["served_model"] == "deterministic-rule-standin"
    assert "Served model deterministic-rule-standin." in result["metadata"]["verdict_text"]
    assert result["metadata"]["llm"]["provenance"]["served_model"] == "deterministic-rule-standin"


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
    settings = Settings(
        sample_mode=False,
        zetaris_api_key=None,
        llm_provider="meterless",
        _env_file=None,
    )
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
