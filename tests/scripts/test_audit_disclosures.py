"""Disclosure reports must preserve inference and exclude incomplete runs."""
import json

import pytest

from app.trace_log import HackathonTrace
from scripts.audit_disclosures import cache_link, eval16_snapshot, inferred_override, v3_audit


@pytest.mark.parametrize("verdict,reason,wanted", [
    ("CANDIDATE", "must beat x/y: did not block: missing licence", True),
    ("CANDIDATE", "all guards passed", False),
    ("NEEDS_REVIEW", "must beat x/y: did not block", False),
])
def test_override_is_only_inferred_from_the_specific_reason_format(verdict, reason, wanted):
    assert inferred_override(verdict, reason) is wanted


def test_cache_timestamp_and_model_name_cannot_substitute_for_call_identity():
    call = {"requested_model": "nvidia/nemotron-a", "served_model": "nvidia/nemotron-a"}
    entry = {**call, "response_text": '{"verdict":"ALREADY_EXISTS"}', "created_at": "same time"}
    assert cache_link(call, [entry])["status"] == "not_linkable"


def test_cache_link_requires_a_unique_exact_identity():
    call = {"requested_model": "nvidia/nemotron-a", "served_model": "nvidia/nemotron-a",
            "prompt_sha256": "p", "input_sha256": "i"}
    entry = {**call, "response_text": '{"verdict":"ALREADY_EXISTS"}', "_sha256": "receipt"}
    assert cache_link(call, [entry])["raw_verdict"] == "ALREADY_EXISTS"
    assert cache_link(call, [entry, entry])["status"] == "ambiguous"
    assert cache_link(call, [{**entry, "input_sha256": "another"}])["status"] == "miss"


def _sealed_run(directory, repo="a/one", *, evidence="zetaris", model="nvidia/nemotron-test", schema="phoenix_eval16"):
    slug = repo.replace("/", "-")
    trace = HackathonTrace(f"zetaris-eval16-{slug}", directory)
    trace.event(agent_name="orchestrator", action="evidence_source", output_summary={"evidence_source": evidence})
    for table in ("discovery_candidates", "prior_art", "demand_signals"):
        trace.event(agent_name="gate1", action="zetaris_query", status="ok",
                    output_summary={"sql": f"SELECT * FROM {schema}.{table}", "query_id": "query", "content_hash": "hash"})
    trace.event(agent_name="orchestrator", action="LLM_CALL", llm={"requested_model": model, "served_model": model})
    trace.event(agent_name="orchestrator", action="DISCOVERY_FILTER", output_summary={"verdict": "CANDIDATE"})
    trace.seal()
    score = {"run_hash": trace.run_hash, "candidates": [{"repo_id": repo}], "ineligible": []}
    (directory / f"scores-{slug}.json").write_text(json.dumps(score), encoding="utf-8")
    return trace


def test_partial_eval16_has_no_aggregate_score(tmp_path):
    _sealed_run(tmp_path)
    cases = [{"repo_id": "a/one", "acceptable": ["CANDIDATE"]}, {"repo_id": "b/two", "acceptable": ["CANDIDATE"]}]
    result = eval16_snapshot(tmp_path, cases)
    assert result["status"] == "incomplete"
    assert result["verified_runs"] == 1
    assert "summary" not in result


def test_complete_eval16_is_explicitly_separate_from_v3(tmp_path):
    _sealed_run(tmp_path)
    result = eval16_snapshot(tmp_path, [{"repo_id": "a/one", "acceptable": ["CANDIDATE"]}])
    assert result["status"] == "complete"
    assert result["data_class"] == "POST_V3_ZETARIS_EVAL16"
    assert result["summary"] == {"judged": 1, "agreed": 1}
    assert "not held-out" in result["claim_scope"]


@pytest.mark.parametrize("kind", ["unpinned", "corrupt", "csv", "other_model", "wrong_verdict"])
def test_invalid_eval16_evidence_never_enters_a_completed_score(tmp_path, kind):
    trace = _sealed_run(tmp_path, evidence="csv" if kind == "csv" else "zetaris",
                        model="another-model" if kind == "other_model" else "nvidia/nemotron-test")
    score_path = tmp_path / "scores-a-one.json"
    score = json.loads(score_path.read_text(encoding="utf-8"))
    if kind == "unpinned":
        score.pop("run_hash")
    if kind == "wrong_verdict":
        score.update(candidates=[], ineligible=[{"repo_id": "a/one", "verdict": "ALREADY_EXISTS"}])
    score_path.write_text(json.dumps(score), encoding="utf-8")
    if kind == "corrupt":
        trace.path.write_text(trace.path.read_text(encoding="utf-8").replace("CANDIDATE", "ALREADY_EXISTS"), encoding="utf-8")
    result = eval16_snapshot(tmp_path, [{"repo_id": "a/one", "acceptable": ["CANDIDATE"]}])
    assert result["status"] == "incomplete"
    assert result["verified_runs"] == 0
    assert "summary" not in result


def test_committed_v3_recomputes_the_disclosure_figures_and_remains_inferred():
    from scripts.audit_disclosures import ROOT

    report = v3_audit(ROOT)
    assert report["figures"]["cases"] == 16
    assert report["figures"]["agreements"] == 10
    assert report["figures"]["false_passes"] == 4
    assert report["candidate_count"] == 12
    assert report["inferred_override_candidates"] == 8
    assert all(row["override_inferred_from_reason"] for row in report["cases"] if row["false_pass"])
    assert not any(row["raw_verdict_recorded"] for row in report["cases"])
    assert not any(row["request_settings_lines"] for row in report["cases"])


def test_a_valid_old_zetaris_trace_cannot_be_relabelled_as_eval16(tmp_path):
    _sealed_run(tmp_path, schema="phoenix")
    result = eval16_snapshot(tmp_path, [{"repo_id": "a/one", "acceptable": ["CANDIDATE"]}])
    assert result["status"] == "incomplete"
    assert result["verified_runs"] == 0
    assert "phoenix_eval16" in result["cases"][0]["detail"]


def test_recorded_mismatch_is_retained_without_relabelling_used_nemotron_answers(tmp_path):
    _sealed_run(tmp_path)
    path = tmp_path / "scores-a-one.json"
    score = json.loads(path.read_text(encoding="utf-8"))
    score["model_mismatch_count"] = 1
    path.write_text(json.dumps(score), encoding="utf-8")
    row = eval16_snapshot(tmp_path, [{"repo_id": "a/one", "acceptable": ["CANDIDATE"]}])["cases"][0]
    assert row["status"] == "verified"
    assert row["model_mismatch_count_recorded"] == 1
    assert row["answers_used"] == row["answers_by_nemotron"] == 1
