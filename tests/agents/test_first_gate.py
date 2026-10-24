"""The first gate: a technically successful revival is not enough if the problem is already solved."""
import json
from pathlib import Path

import pytest

from app.first_gate import apply_first_gate
from app.investigation import InvestigationOrchestrator
from config.settings import Settings


def _run(repo: str, tmp_path: Path, gate, repairs: int = 0) -> dict:
    settings = Settings(
        sample_mode=True,
        hmem_store_path=str(tmp_path / "hmem.json"),
        log_dir=str(tmp_path / "logs"),
        _env_file=None,
    )
    return InvestigationOrchestrator(
        repo, "sample", repairs, settings, hmem_path=tmp_path / "hmem.json", first_gate=gate
    ).run()


def _gate(verdict: str, calls: list | None = None):
    def gate(repository: str, run_id: str) -> dict:
        if calls is not None:
            calls.append(repository)
        return {"verdict": verdict, "reason": "plopjs/plop already generates files from templates", "report": "reports/x/report.html", "run_id": run_id}

    return gate


def _actions(result: dict) -> list[str]:
    return [json.loads(l)["action"] for l in Path(result["log_file"]).read_text(encoding="utf-8").splitlines()]


def test_already_exists_blocks_a_successful_revival(tmp_path):
    res = _run("sample/phoenix-ledger", tmp_path, _gate("ALREADY_EXISTS"))
    assert res["decision"] == "REVIVED_NOT_RECOMMENDED"
    assert res["state"] == "REVIVED_NOT_RECOMMENDED"
    assert "RESURRECTED" != res["decision"]
    assert "FIRST_GATE" in _actions(res)
    assert not res["metadata"]["revival_prd"]
    assert "plopjs/plop" in res["metadata"]["reason"]


def test_a_repo_with_no_mature_alternative_passes_the_gate(tmp_path):
    calls: list[str] = []
    res = _run("sample/phoenix-ledger", tmp_path, _gate("CANDIDATE", calls))
    assert res["decision"] == "CANDIDATE"
    assert calls == ["sample/phoenix-ledger"]
    assert res["metadata"]["revival_prd"]


@pytest.mark.parametrize("verdict", ["NEEDS_REVIEW", "NO_GO", "UNJUDGED", ""])
def test_any_other_verdict_needs_a_human(tmp_path, verdict):
    res = _run("sample/phoenix-ledger", tmp_path, _gate(verdict))
    assert res["decision"] == "NEEDS_REVIEW"
    assert res["state"] == "NEEDS_REVIEW"
    assert not res["metadata"]["revival_prd"]


def test_search_failure_is_never_a_pass(tmp_path):
    def broken(repository: str, run_id: str) -> dict:
        raise RuntimeError("web source error: exa 429")

    res = _run("sample/phoenix-ledger", tmp_path, broken)
    assert res["decision"] == "NEEDS_REVIEW"
    assert "exa 429" in res["metadata"]["reason"]


def test_gate_is_not_asked_for_repos_that_already_failed_or_were_rejected(tmp_path):
    calls: list[str] = []
    assert _run("sample/generic-qr", tmp_path / "a", _gate("CANDIDATE", calls))["decision"] == "NO_GO"
    assert _run("sample/field-notes", tmp_path / "b", _gate("CANDIDATE", calls), repairs=1)["decision"] == "RESURRECTION_FAILED"
    assert calls == []


def test_resurrected_plus_already_exists_is_never_resurrected():
    decision, reason = apply_first_gate("RESURRECTED", "stage revive succeeded", {"verdict": "ALREADY_EXISTS", "reason": "plop"})
    assert decision == "REVIVED_NOT_RECOMMENDED"
    assert "RESURRECTED" in reason  # the technical result is still stated honestly
    assert apply_first_gate("RESURRECTED", "ok", {"verdict": "CANDIDATE"})[0] == "RESURRECTED"
    assert apply_first_gate("RESURRECTED", "ok", None, "boom")[0] == "NEEDS_REVIEW"
    assert apply_first_gate("RESURRECTION_FAILED", "failed", {"verdict": "CANDIDATE"})[0] == "RESURRECTION_FAILED"


def test_sample_mode_without_a_gate_is_labelled_not_silent(tmp_path):
    res = _run("sample/phoenix-ledger", tmp_path, None)
    assert res["decision"] == "CANDIDATE"
    assert "FIRST_GATE_SKIPPED" in _actions(res)