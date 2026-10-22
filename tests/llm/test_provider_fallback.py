from app.adapters.meterless import MeterlessError
from app.investigation import InvestigationOrchestrator
from app.llm.provider import BoundLLM
from config.settings import Settings


class _Scripted:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def complete(self, messages, model=None):
        self.calls.append(model)
        outcome = self.results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        self.last_requested_model = model
        self.last_served_model = "served-" + str(model)
        return outcome


def test_timeout_retries_then_uses_the_next_nightshift_lane():
    inner = _Scripted(
        [
            MeterlessError("Meterless chat timed out after 1s."),
            MeterlessError("Meterless chat timed out after 1s."),
            MeterlessError("Meterless chat timed out after 1s."),
            MeterlessError("Meterless chat timed out after 1s."),
            '{"decision":"CANDIDATE"}',
        ]
    )
    slept = []
    llm = BoundLLM(
        inner,
        "meterless",
        "nvidia/nemotron-3-super-120b-a12b",
        False,
        lanes=("nvidia/nemotron-3-super-120b-a12b", "meterless/auto"),
        sleeper=lambda seconds: slept.append(seconds),
    )
    text = llm.complete([{"role": "user", "content": "hi"}])
    assert text == '{"decision":"CANDIDATE"}'
    assert inner.calls[0] == "nvidia/nemotron-3-super-120b-a12b"
    assert inner.calls[-1] == "meterless/auto"
    assert slept == [2, 4, 8]
    assert llm.fallback_log[0]["reason"] == "timeout"
    assert llm.fallback_log[0]["requested_model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert "nvidia" not in llm.provider or llm.provider == "meterless"


def test_unauthorized_does_not_retry():
    inner = _Scripted([MeterlessError("Meterless rejected the runtime token.", status_code=401)])
    slept = []
    llm = BoundLLM(
        inner,
        "meterless",
        "nvidia/nemotron-3-super-120b-a12b",
        False,
        lanes=("nvidia/nemotron-3-super-120b-a12b", "meterless/auto"),
        sleeper=lambda seconds: slept.append(seconds),
    )
    try:
        llm.complete([{"role": "user", "content": "hi"}])
    except MeterlessError:
        pass
    else:
        raise AssertionError("expected the unauthorized error")
    assert slept == []
    assert len(inner.calls) == 1


def test_all_lanes_down_still_returns_a_verdict(tmp_path):
    class _Down:
        def complete(self, messages, model=None):
            raise MeterlessError("Meterless chat failed with HTTP 429.", status_code=429)

    llm = BoundLLM(
        _Down(),
        "meterless",
        "nvidia/nemotron-3-super-120b-a12b",
        False,
        lanes=("nvidia/nemotron-3-super-120b-a12b",),
        sleeper=lambda _seconds: None,
    )
    settings = Settings(
        sample_mode=True,
        hmem_store_path=str(tmp_path / "hmem.json"),
        log_dir=str(tmp_path / "logs"),
        _env_file=None,
    )
    result = InvestigationOrchestrator(
        "sample/phoenix-ledger",
        "sample",
        0,
        settings,
        llm=llm,
        hmem_path=tmp_path / "hmem.json",
    ).run()
    assert result["decision"] == "CANDIDATE"
    assert result["metadata"]["llm"]["degraded"] is True
    actions = [
        __import__("json").loads(line)["action"]
        for line in __import__("pathlib").Path(result["log_file"]).read_text(encoding="utf-8").splitlines()
    ]
    assert "PROVIDER_DEGRADED" in actions
    assert "LLM_FALLBACK" in actions


def test_chat_timeout_stays_inside_the_step_budget():
    settings = Settings(_env_file=None)
    assert settings.chat_timeout_seconds() < settings.agent_step_timeout_seconds
    assert settings.markovian_max_chunks > 8
