"""Synthetic strict-contract counterexamples; no claim of held-out model quality."""
import json
import socket

import pytest

from app.agents import substitution
from app.agents.relevance import RelevanceClient, _row_from_entry, filter_relevant_alternatives, judge_alternative_relevance, judge_discovery
from app.trace_log import HackathonTrace
from scripts.verify_trace import verify

SOURCE = "Write events from the desktop calendar into the team calendar using a self-hosted server."
ALT = "A self-hosted server writes events from desktop calendars into team calendars."
CAND = {"repo_id": "synth/calendar-writer", "description": SOURCE, "readme_excerpt": SOURCE, "stars": 2}
PROBLEM = {"problem": "Publish team calendar events", "candidate_evidence": SOURCE}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("network forbidden")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setenv(substitution.FLAG, "1")
    monkeypatch.delenv("PHOENIX_UNKNOWN_BLOCKER_REVIEW", raising=False)


def proof(**changes):
    return {"candidate_quote": SOURCE, "alternative_quote": ALT, "concrete_product": True,
            **{key: True for key in substitution.AXES}, **changes}


def row(**changes):
    return {"alternative_id": "synth/other", "url": "https://github.com/synth/other", "source": "github",
            "same_problem": True, "overlap": "full", "stars": 2000, "archived": False,
            "licence_spdx": "MIT", "pushed_at": "2026-10-20", "description": ALT,
            "quotes": [ALT], "substitution": proof(), **changes}


def judge(tmp_path, alternative, raw="CANDIDATE", trace=None):
    client = RelevanceClient(cache_dir=tmp_path, mock_handler=lambda p, s: json.dumps({
        "verdict": raw, "reason": "model claim", "problem_quote": SOURCE,
        "alternative_repo": "synth/other", "alternative_quote": ALT,
    }))
    return judge_discovery(CAND, [], [], client=client, problem_alternatives=[alternative], trace=trace)


@pytest.mark.parametrize("raw", ["CANDIDATE", "ALREADY_EXISTS"])
def test_concrete_same_context_product_can_still_refuse(tmp_path, raw):
    assert judge(tmp_path, row(), raw)[0] == "ALREADY_EXISTS"


@pytest.mark.parametrize("axis", substitution.AXES)
@pytest.mark.parametrize("raw", ["CANDIDATE", "ALREADY_EXISTS"])
def test_different_direction_deployment_operation_or_user_cannot_refuse(tmp_path, axis, raw):
    assert judge(tmp_path, row(substitution=proof(**{axis: False})), raw)[0] == "CANDIDATE"


@pytest.mark.parametrize("bad", [None, {}, proof(candidate_quote="invented candidate text"),
    proof(alternative_quote="invented alternative text"), proof(concrete_product=False),
    proof(same_direction="true"), proof(same_direction=None), proof(concrete_product="true")])
@pytest.mark.parametrize("raw", ["CANDIDATE", "ALREADY_EXISTS"])
def test_missing_or_invalid_contract_requires_review_instead_of_a_refusal_or_pass(tmp_path, bad, raw):
    assert judge(tmp_path, row(substitution=bad), raw)[0] == "NEEDS_REVIEW"


def test_directory_on_an_official_docs_host_is_not_automatically_a_product(tmp_path):
    alternative = row(alternative_id="web:https://docs.calendar.example/providers", source="web",
        url="https://docs.calendar.example/providers", description="Calendar provider directory. " + ALT,
        substitution=proof(concrete_product=False))
    assert judge(tmp_path, alternative)[0] == "NEEDS_REVIEW"


@pytest.mark.parametrize("batch", [False, True])
def test_contract_survives_both_parsers(tmp_path, batch):
    raw = row()
    payload = {"same_problem": True, "overlap": "full", "quotes": [ALT], "substitution": proof()}
    if batch:
        parsed = _row_from_entry(CAND["repo_id"], raw, ALT, payload, PROBLEM, None)
    else:
        client = RelevanceClient(cache_dir=tmp_path, mock_handler=lambda p, s: json.dumps(payload))
        parsed, _ = judge_alternative_relevance(PROBLEM, CAND["repo_id"], raw, client)
    assert parsed["substitution_status"] == "quoted_match"
    assert parsed["substitution"] == proof()


def test_filter_supplies_actual_candidate_text_not_just_problem_paraphrase(tmp_path):
    observed = []
    def respond(prompt, system):
        observed.append(prompt)
        return json.dumps({"same_problem": True, "overlap": "full", "quotes": [ALT], "substitution": proof()})
    client = RelevanceClient(cache_dir=tmp_path, mock_handler=respond)
    verified = filter_relevant_alternatives({"problem": "A paraphrased problem"}, CAND["repo_id"], [row()], client=client, candidate=CAND)
    assert SOURCE in observed[0]
    assert verified[0]["substitution_status"] == "quoted_match"


def test_enabling_contract_uses_a_different_cache_entry(tmp_path, monkeypatch):
    calls = []
    def respond(prompt, system):
        calls.append(system)
        return json.dumps({"same_problem": True, "overlap": "full", "quotes": [ALT], "substitution": proof()})
    client = RelevanceClient(cache_dir=tmp_path, mock_handler=respond)
    monkeypatch.delenv(substitution.FLAG)
    judge_alternative_relevance(PROBLEM, CAND["repo_id"], row(), client)
    monkeypatch.setenv(substitution.FLAG, "1")
    judge_alternative_relevance(PROBLEM, CAND["repo_id"], row(), client)
    judge_alternative_relevance(PROBLEM, CAND["repo_id"], row(), client)
    assert len(calls) == 2
    assert substitution.INSTRUCTION not in calls[0] and substitution.INSTRUCTION in calls[1]


def test_strict_gate_rechecks_contract_and_records_status_in_sealed_trace(tmp_path):
    trace = HackathonTrace("strict-contract", tmp_path / "logs")
    alternative = row(substitution=None, substitution_status="quoted_match")  # A stale label is insufficient.
    assert judge(tmp_path / "cache", alternative, trace=trace)[0] == "NEEDS_REVIEW"
    assert alternative["substitution_status"] == "quoted_match"  # Input is not modified.
    trace.seal()
    assert verify(trace.path, expected_run_hash=trace.run_hash)[0]
    gate = next(json.loads(line) for line in trace.path.read_text().splitlines() if json.loads(line)["action"] == "GATE_DECISION")
    output = json.loads(gate["output_summary"])
    assert output["substitution_status"] == "unverified"
    assert output["path"] == "SUBSTITUTION_UNVERIFIED"
    assert output["gate"]["checks"] == {"A": "unknown", **{key: "not_reached" for key in "BCDE"}}


def test_flag_is_off_when_unset(monkeypatch):
    monkeypatch.delenv(substitution.FLAG)
    assert not substitution.enabled()
