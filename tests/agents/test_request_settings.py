"""New LLM lines record the HTTP sampling flags that were sent. Old traces stay valid."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.agents.relevance import RelevanceClient
from app.trace_log import HackathonTrace, kept_request_settings, request_settings_sha256
from scripts.verify_trace import main as verify_main
from scripts.verify_trace import verify

FAST = "nvidia/nemotron-3.5-lightning-30b-a3b"
DEEP = "nvidia/nemotron-3-ultra-550b-a55b"

ROOT = Path(__file__).resolve().parents[2]
NEMO = "nvidia/nemotron-3-super-120b-a12b"

COMMITTED = (
    (
        "data/replays/dsregoitia-calendarsyncproject/trace.jsonl",
        "2eb5a1e3157e3f5a148ede12fc06d4ca6f4e7a8a7262d7f2c0a5326569a56cd0",
    ),
    (
        "data/replays/tharani-2006-invoice-generator-application/trace.jsonl",
        "cfbe3cc16adb981ee3d7e5ec240424505eb9fe32fe10f1f54b3110f3c56f340f",
    ),
    (
        "docs/rescue/trace-rescue-0xmh__fichero-printer-20261027T001117Z.jsonl",
        "51e828e538ed8f05dda15648fc673c3461ca310a87c0af77c53482e1f0a086c4",
    ),
)


def test_committed_traces_still_verify_with_the_same_run_hash():
    for relative, expected in COMMITTED:
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        assert "request_settings" not in text
        ok, detail = verify(path, expected_run_hash=expected)
        assert ok, detail
        assert detail == expected


def test_kept_request_settings_drops_unknown_and_secret_like_keys():
    kept = kept_request_settings({
        "temperature": 0,
        "top_p": 0.95,
        "max_tokens": 128,
        "reasoning_effort": "none",
        "reasoning_budget": 1024,
        "enable_thinking": False,
        "chat_template_kwargs": {"enable_thinking": False, "system": "secret prompt text"},
        "authorization": "Bearer secret-value",
        "api_key": "nvapi-" + "a" * 30,
        "token": "sk-ant-" + "b" * 30,
        "base_url": "https://integrate.api.nvidia.com/v1",
        "headers": {"Authorization": "Bearer secret-value"},
        "messages": [{"role": "user", "content": "secret prompt text"}],
        "seed": 7,
        "min_p": 0.05,
    })
    assert kept == {
        "chat_template_kwargs": {"enable_thinking": False},
        "max_tokens": 128,
        "reasoning_budget": 1024,
        "reasoning_effort": "none",
        "temperature": 0,
        "top_p": 0.95,
    }
    assert kept_request_settings(kept) == kept
    dumped = json.dumps(kept)
    for secret in ("secret", "Bearer", "nvapi-", "sk-ant-", "https://", "base_url", "messages", "seed"):
        assert secret not in dumped
    # A token-shaped value on an allowlisted key is dropped, not stored.
    poisoned = kept_request_settings({"reasoning_effort": "nvapi-" + "c" * 30, "temperature": "0"})
    assert poisoned == {}


def test_sent_settings_are_traced_and_a_different_body_gets_its_own_cache_entry(tmp_path: Path, monkeypatch):
    for name in ("PHOENIX_LLM_EXTRA_BODY", "PHOENIX_LLM_MAX_TOKENS", "MODEL_NAME", "METERLESS_MODEL", "PHOENIX_DEEP_MODEL", "MODEL_NAME_DEEP"):
        monkeypatch.delenv(name, raising=False)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        body = json.loads(request.content)
        assert body["temperature"] == 0
        assert body["max_tokens"] == 128
        assert body["top_p"] == 0.95
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert "messages" in body
        return httpx.Response(200, json={"model": NEMO, "choices": [{"message": {"content": '{"ok": true}'}}]})

    trace = HackathonTrace("settings", tmp_path / "logs")
    cache = tmp_path / "cache"
    first = RelevanceClient(
        token="rt-test",
        model=NEMO,
        cache_dir=cache,
        transport=httpx.MockTransport(handler),
        trace=trace,
        max_tokens=128,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}, "top_p": 0.95},
    )
    first.complete("same prompt", "same system", repo_id="o/r", step="problem")
    # The same extra body (another key order) replays from the cache. The replay is not a
    # new HTTP call, so it must not claim those settings were sent.
    second = RelevanceClient(
        token="rt-test",
        model=NEMO,
        cache_dir=cache,
        transport=httpx.MockTransport(handler),
        trace=trace,
        max_tokens=128,
        extra_body={"top_p": 0.95, "chat_template_kwargs": {"enable_thinking": False}},
    )
    second.complete("same prompt", "same system", repo_id="o/r", step="problem")
    assert calls["n"] == 1
    assert len(list(cache.glob("*.json"))) == 1
    cached = next(cache.glob("*.json")).read_text(encoding="utf-8")
    assert '"request_settings"' not in cached  # only a hash of the settings is stored, not the values
    assert "enable_thinking" not in cached
    stored = json.loads(cached)
    assert stored["request_settings_sha256"] == request_settings_sha256({
        "chat_template_kwargs": {"enable_thinking": False},
        "max_tokens": 128,
        "temperature": 0,
        "top_p": 0.95,
    })
    # A different extra body is part of the cache key: a new call with its own entry.
    other_calls = {"n": 0}

    def other_handler(request: httpx.Request) -> httpx.Response:
        other_calls["n"] += 1
        body = json.loads(request.content)
        assert body["temperature"] == 0.6
        assert body["chat_template_kwargs"] == {"enable_thinking": True}
        return httpx.Response(200, json={"model": NEMO, "choices": [{"message": {"content": '{"ok": false}'}}]})

    third = RelevanceClient(
        token="rt-test",
        model=NEMO,
        cache_dir=cache,
        transport=httpx.MockTransport(other_handler),
        trace=trace,
        max_tokens=128,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}, "temperature": 0.6},
    )
    assert third.complete("same prompt", "same system", repo_id="o/r", step="problem")[0] == '{"ok": false}'
    assert other_calls["n"] == 1
    assert len(list(cache.glob("*.json"))) == 2
    lines = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    fresh, replay, other = [line for line in lines if line.get("action") == "LLM_CALL"]
    expected = {
        "chat_template_kwargs": {"enable_thinking": False},
        "max_tokens": 128,
        "temperature": 0,
        "top_p": 0.95,
    }
    digest = request_settings_sha256(expected)
    assert fresh["request_settings"] == expected
    assert fresh["request_settings_sha256"] == digest
    fresh_summary = json.loads(fresh["output_summary"])
    assert fresh_summary["tier"] == "fast"
    assert fresh_summary["request_settings"] == expected
    assert fresh_summary["request_settings_sha256"] == digest
    assert "request_settings" not in replay
    assert "request_settings" not in json.loads(replay["output_summary"])
    assert other["request_settings"]["temperature"] == 0.6
    assert other["request_settings"]["chat_template_kwargs"] == {"enable_thinking": True}
    # The hash stored in the cache is the hash of the settings the trace recorded for the real call.
    assert stored["request_settings_sha256"] == fresh["request_settings_sha256"]
    assert "messages" not in json.dumps(fresh["request_settings"])
    assert "same prompt" not in trace.path.read_text(encoding="utf-8")
    trace.seal()
    ok, detail = verify(trace.path)
    assert ok, detail


def _reply(model: str) -> httpx.Response:
    return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": '{"ok": true}'}}]})


def _summary(line: dict) -> dict:
    summary = json.loads(line["output_summary"])
    assert isinstance(summary, dict)
    return summary


def test_fast_deep_and_fallback_calls_record_settings_and_the_chain_verifies(tmp_path: Path, monkeypatch, capsys):
    for name in (
        "PHOENIX_LLM_EXTRA_BODY", "PHOENIX_LLM_DEEP_EXTRA_BODY", "PHOENIX_LLM_MAX_TOKENS",
        "MODEL_NAME", "METERLESS_MODEL", "PHOENIX_DEEP_MODEL", "MODEL_NAME_DEEP",
        "PHOENIX_NEMOTRON_POLICY", "NVIDIA_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("app.agents.relevance.time.sleep", lambda _s: None)
    api_key = "nvapi-test-key-000000000000"

    def nightshift(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["messages"][-1]["content"]
        if "FALLBACK" in prompt:
            return _reply("gemini-3.1-flash-lite")
        return _reply(body["model"])

    def nvidia(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {api_key}"
        return _reply(FAST)

    trace = HackathonTrace("tiers", tmp_path / "logs", secrets=[api_key])
    client = RelevanceClient(
        token="rt-test",
        model=FAST,
        deep_model=DEEP,
        cache_dir=tmp_path / "cache",
        transport=httpx.MockTransport(nightshift),
        nvidia_transport=httpx.MockTransport(nvidia),
        trace=trace,
        max_tokens=256,
        nemotron_policy="fallback-nvidia",
        nvidia_api_key=api_key,
        extra_body={
            "chat_template_kwargs": {"enable_thinking": False, "system": "do not record this prompt"},
            "reasoning_effort": "none",
            "top_p": 0.95,
            "api_key": api_key,
            "base_url": "https://integrate.api.nvidia.com/v1",
            "messages": [{"role": "user", "content": "prompt text"}],
            "headers": {"Authorization": f"Bearer {api_key}"},
        },
        deep_extra_body={
            "chat_template_kwargs": {"enable_thinking": True},
            "temperature": 0.6,
            "top_p": 0.95,
            "reasoning_effort": "high",
        },
    )
    client.complete("fast call", "system", repo_id="o/r", step="alternative")
    client.complete("deep call", "system", repo_id="o/r", step="discovery")
    client.complete("FALLBACK call", "system", repo_id="o/r", step="coverage")
    trace.seal()
    text = trace.path.read_text(encoding="utf-8")
    for secret in (api_key, "integrate.api.nvidia.com", "do not record this prompt", "FALLBACK call", "Bearer"):
        assert secret not in text
    lines = [json.loads(line) for line in text.splitlines() if line.strip()]
    calls = [line for line in lines if line.get("action") == "LLM_CALL"]
    fast = next(line for line in calls if json.loads(line["input_summary"])["step"] == "alternative")
    deep = next(line for line in calls if json.loads(line["input_summary"])["step"] == "discovery")
    fallback = next(
        line for line in calls
        if json.loads(line["input_summary"])["step"] == "coverage" and line["status"] == "ok"
    )
    fast_summary = _summary(fast)
    deep_summary = _summary(deep)
    fallback_summary = _summary(fallback)
    assert fast_summary["tier"] == "fast"
    assert deep_summary["tier"] == "deep"
    assert fallback_summary["tier"] == "fast"
    assert fallback_summary["provider"] == "nvidia-direct"
    fast_settings = {
        "chat_template_kwargs": {"enable_thinking": False},
        "max_tokens": 256,
        "reasoning_effort": "none",
        "temperature": 0,
        "top_p": 0.95,
    }
    deep_settings = {
        "chat_template_kwargs": {"enable_thinking": True},
        "max_tokens": 256,
        "reasoning_effort": "high",
        "temperature": 0.6,
        "top_p": 0.95,
    }
    assert fast_summary["request_settings"] == fast["request_settings"] == fast_settings
    assert deep_summary["request_settings"] == deep["request_settings"] == deep_settings
    assert fallback_summary["request_settings"] == fallback["request_settings"] == fast_settings
    for line, settings in ((fast, fast_settings), (deep, deep_settings), (fallback, fast_settings)):
        digest = request_settings_sha256(settings)
        assert line["request_settings_sha256"] == digest
        assert _summary(line)["request_settings_sha256"] == digest
    cached_files = list((tmp_path / "cache").glob("*.json"))
    assert cached_files
    for cached_file in cached_files:
        cached_text = cached_file.read_text(encoding="utf-8")
        assert '"request_settings"' not in cached_text
        assert "enable_thinking" not in cached_text
    ok, detail = verify(trace.path)
    assert ok, detail
    assert verify_main([str(trace.path)]) == 0
    reported = capsys.readouterr().out
    assert "request_settings: present on" in reported
    assert "informational; absence is not a failure" in reported
    assert "of 0 " not in reported


def test_verify_trace_reports_missing_settings_on_a_scored_replay_without_failing(capsys):
    path = ROOT / "data/replays/dsregoitia-calendarsyncproject/trace.jsonl"
    assert "request_settings" not in path.read_text(encoding="utf-8")
    assert verify_main([str(path), "--expected-run-hash", "2eb5a1e3157e3f5a148ede12fc06d4ca6f4e7a8a7262d7f2c0a5326569a56cd0"]) == 0
    out = capsys.readouterr().out
    assert "request_settings: present on 0 of" in out
    assert "informational; absence is not a failure" in out
