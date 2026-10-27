"""New LLM lines record the HTTP sampling flags that were sent. Old traces stay valid."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from app.agents.relevance import RelevanceClient
from app.trace_log import HackathonTrace, kept_request_settings
from scripts.verify_trace import verify

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


def test_kept_request_settings_drops_a_token_and_unknown_keys():
    kept = kept_request_settings({
        "temperature": 0,
        "enable_thinking": False,
        "reasoning_effort": "none",
        "authorization": "Bearer secret-value",
        "messages": [{"role": "user", "content": "secret"}],
        "reasoning_budget": "nvapi-" + "a" * 40,
    })
    assert kept == {"temperature": 0, "enable_thinking": False, "reasoning_effort": "none"}


def test_sent_settings_are_traced_and_are_not_part_of_the_cache_key(tmp_path: Path, monkeypatch):
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
    # A different extra body must not change the cache key. The replay is not a new HTTP call,
    # so it must not claim those settings were sent.
    second = RelevanceClient(
        token="rt-test",
        model=NEMO,
        cache_dir=cache,
        transport=httpx.MockTransport(handler),
        trace=trace,
        max_tokens=128,
        extra_body={"chat_template_kwargs": {"enable_thinking": True}, "temperature": 0.6},
    )
    second.complete("same prompt", "same system", repo_id="o/r", step="problem")
    assert calls["n"] == 1
    assert len(list(cache.glob("*.json"))) == 1
    cached = next(cache.glob("*.json")).read_text(encoding="utf-8")
    assert "request_settings" not in cached
    assert "enable_thinking" not in cached
    lines = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    fresh, replay = [line for line in lines if line.get("action") == "LLM_CALL"]
    assert fresh["request_settings"] == {
        "enable_thinking": False,
        "temperature": 0,
        "top_p": 0.95,
        "max_tokens": 128,
    }
    assert "request_settings" not in replay
    assert "messages" not in json.dumps(fresh["request_settings"])
    trace.seal()
    ok, detail = verify(trace.path)
    assert ok, detail
