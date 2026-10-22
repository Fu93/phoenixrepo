import json
from pathlib import Path

from app.agents.llm_judge import call_llm_judge, combine
from app.agents.validate import Critique
from app.adapters.sample_zetaris import SampleZetaris
from app.investigation import InvestigationOrchestrator
from app.llm.provider import BoundLLM
from app.llm.standin import SampleStandIn
from config.settings import Settings


class _Scripted:
    def __init__(self, judge_raw_for_role):
        self.standin = SampleStandIn()
        self.judge_raw_for_role = judge_raw_for_role
        self.last_requested_model = ""
        self.last_served_model = None

    def complete(self, messages, model=None):
        self.last_requested_model = "meterless/auto"
        self.last_served_model = "nvidia/nemotron-3-super-120b-a12b"
        text = messages[-1]["content"]
        if "ROLE: llm_judge" in text:
            if '"role": "market_judge"' in text:
                return self.judge_raw_for_role("market_judge")
            return self.judge_raw_for_role("archaeologist")
        return self.standin.complete(messages, model=model)


def _bound(scripted: _Scripted) -> BoundLLM:
    return BoundLLM(scripted, "meterless", "meterless/auto", False)


def test_unparsed_judge_does_not_invent_a_reject():
    class _Raw:
        def complete(self, messages):
            self.requested_model = "meterless/auto"
            self.served_model = "nvidia/nemotron-3-super-120b-a12b"
            return "I need more information before I can answer."

    outcome = call_llm_judge(_Raw(), subject={"role": "market_judge"})
    assert outcome.parsed is False
    assert outcome.decision == "unparsed"
    assert outcome.latency_ms >= 0
    guard = Critique(False, target_agent="market_judge")
    combined, note = combine(guard, outcome)
    assert combined.rejected is False
    assert note == ""


def test_reject_json_after_reasoning_is_a_reject():
    class _Raw:
        def complete(self, messages):
            self.requested_model = "nvidia/nemotron-3-super-120b-a12b"
            self.served_model = "nvidia/nemotron-3-ultra-550b-a55b"
            return (
                "The rows do not mention a waiting user base.\n"
                '{"decision":"reject","reason":"No cited row shows demand.","claim_ids":["CLM-DEMAND"]}'
            )

    outcome = call_llm_judge(_Raw(), subject={"role": "archaeologist"})
    guard = Critique(False, target_agent="archaeologist")
    combined, note = combine(guard, outcome)
    assert outcome.served_model == "nvidia/nemotron-3-ultra-550b-a55b"
    assert combined.rejected is True
    assert "demand" in combined.reason
    assert combined.claim_ids == ["CLM-DEMAND"]
    assert note == ""


def test_missing_evidence_guard_overrides_an_accept():
    class _Raw:
        def complete(self, messages):
            self.requested_model = "meterless/auto"
            self.served_model = "meterless/auto"
            return '{"decision":"accept","reason":"fine","claim_ids":[]}'

    outcome = call_llm_judge(_Raw(), subject={})
    guard = Critique(True, "CLM-DEMAND has no evidence id.", ["CLM-DEMAND"], "archaeologist")
    combined, note = combine(guard, outcome)
    assert combined.rejected is True
    assert "no evidence" in combined.reason
    assert "overrode" in note


def test_live_market_judge_reject_is_logged_and_does_not_change_the_policy(tmp_path):
    def judge_raw(role):
        if role == "market_judge":
            return (
                "<think>unsupported</think>\n"
                '{"decision":"reject","reason":"The explanation adds a user community the rows do not show.",'
                '"claim_ids":["CLM-DECISION"]}'
            )
        return '{"decision":"accept","reason":"The cited rows support these claims.","claim_ids":[]}'

    settings = Settings(
        sample_mode=False,
        zetaris_api_key=None,
        llm_provider="meterless",
        hmem_store_path=str(tmp_path / "hmem.json"),
        log_dir=str(tmp_path / "logs"),
        _env_file=None,
    )
    result = InvestigationOrchestrator(
        "sample/generic-qr",
        "live",
        0,
        settings,
        zetaris=SampleZetaris(cache_dir=Path("data/sample")),
        llm=_bound(_Scripted(judge_raw)),
        hmem_path=tmp_path / "hmem.json",
    ).run()
    assert result["decision"] == "NO_GO"
    assert result["metadata"]["llm"]["sample_standin"] is False
    assert result["metadata"]["llm"]["explanation_accepted"] is False
    records = [json.loads(line) for line in Path(result["log_file"]).read_text(encoding="utf-8").splitlines()]
    judged = [record for record in records if record["action"] == "LLM_JUDGE"]
    assert judged
    market = [json.loads(record["output_summary"]) for record in judged if '"market_judge"' in record["input_summary"]]
    assert market
    assert market[-1]["raw_decision"] == "reject"
    inputs = [json.loads(record["input_summary"]) for record in judged]
    assert inputs[0]["requested_model"] == "meterless/auto"
    assert inputs[0]["served_model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert "latency_ms" in inputs[0]
    archaeologist = [
        json.loads(record["output_summary"])
        for record in judged
        if json.loads(record["input_summary"])["role"] == "archaeologist"
    ]
    assert any(item["guard_override"] for item in archaeologist)


def _run_live(tmp_path, repo: str, judge_raw_for_role):
    settings = Settings(
        sample_mode=False,
        zetaris_api_key=None,
        llm_provider="meterless",
        hmem_store_path=str(tmp_path / "hmem.json"),
        log_dir=str(tmp_path / "logs"),
        _env_file=None,
    )
    return InvestigationOrchestrator(
        repo,
        "live",
        0,
        settings,
        zetaris=SampleZetaris(cache_dir=Path("data/sample")),
        llm=_bound(_Scripted(judge_raw_for_role)),
        hmem_path=tmp_path / "hmem.json",
    ).run()


def test_always_reject_judge_still_produces_the_policy_verdict(tmp_path):
    def judge_raw(role):
        del role
        return '{"decision":"reject","reason":"advisory reject","claim_ids":["CLM-DECISION"]}'

    result = _run_live(tmp_path, "sample/phoenix-ledger", judge_raw)
    assert result["decision"] == "CANDIDATE"
    assert result["metadata"]["llm"]["judge"]["advisory"] is True
    assert result["metadata"]["llm"]["judge"]["exhausted"] is True
    assert "deterministic policy verdict was kept" in result["metadata"]["verdict_text"]
    actions = [
        json.loads(line)["action"]
        for line in Path(result["log_file"]).read_text(encoding="utf-8").splitlines()
    ]
    assert "LLM_JUDGE" in actions
    assert "LLM_JUDGE_EXHAUSTED" in actions


def test_judge_timeout_is_logged_and_the_policy_verdict_remains(tmp_path):
    from app.adapters.meterless import MeterlessError

    def judge_raw(role):
        del role
        raise MeterlessError("Meterless chat timed out after 30s.")

    result = _run_live(tmp_path, "sample/generic-qr", judge_raw)
    assert result["decision"] == "NO_GO"
    judge = result["metadata"]["llm"]["judge"]
    assert judge["failure"] == "timeout"
    assert "did not complete (timeout)" in result["metadata"]["verdict_text"]
    records = [json.loads(line) for line in Path(result["log_file"]).read_text(encoding="utf-8").splitlines()]
    judged = [record for record in records if record["action"] == "LLM_JUDGE"]
    assert judged
    assert judged[0]["status"] == "timeout"
    assert "unavailable" in judged[0]["output_summary"]


def test_judge_401_is_logged_and_the_policy_verdict_remains(tmp_path):
    from app.adapters.meterless import MeterlessError

    def judge_raw(role):
        del role
        raise MeterlessError(
            "Meterless rejected the runtime token.",
            status_code=401,
            error_type="MeterlessUnauthorized",
        )

    result = _run_live(tmp_path, "sample/generic-qr", judge_raw)
    assert result["decision"] == "NO_GO"
    assert result["metadata"]["llm"]["judge"]["failure"] == "unauthorized"
    assert "did not complete (unauthorized)" in result["metadata"]["verdict_text"]
    records = [json.loads(line) for line in Path(result["log_file"]).read_text(encoding="utf-8").splitlines()]
    judged = [record for record in records if record["action"] == "LLM_JUDGE"]
    assert judged[0]["status"] == "unauthorized"


def test_judge_generic_exception_is_logged_and_the_policy_verdict_remains(tmp_path):
    def judge_raw(role):
        del role
        raise RuntimeError("judge connector reset")

    result = _run_live(tmp_path, "sample/generic-qr", judge_raw)
    assert result["decision"] == "NO_GO"
    assert result["metadata"]["llm"]["judge"]["failure"] == "error"
    assert "did not complete (error)" in result["metadata"]["verdict_text"]
    records = [json.loads(line) for line in Path(result["log_file"]).read_text(encoding="utf-8").splitlines()]
    judged = [record for record in records if record["action"] == "LLM_JUDGE"]
    assert judged
    assert judged[0]["status"] == "error"
    assert "unavailable" in judged[0]["output_summary"]


def test_sample_mode_does_not_call_the_llm_judge(tmp_path):
    settings = Settings(
        sample_mode=True,
        hmem_store_path=str(tmp_path / "hmem.json"),
        log_dir=str(tmp_path / "logs"),
        _env_file=None,
    )
    result = InvestigationOrchestrator(
        "sample/generic-qr",
        "sample",
        0,
        settings,
        hmem_path=tmp_path / "hmem.json",
    ).run()
    actions = [
        json.loads(line)["action"]
        for line in Path(result["log_file"]).read_text(encoding="utf-8").splitlines()
    ]
    assert "LLM_JUDGE" not in actions
    assert result["metadata"]["llm"]["sample_standin"] is True
