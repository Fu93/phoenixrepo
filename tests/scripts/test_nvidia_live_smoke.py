from __future__ import annotations

from scripts import nvidia_live_smoke as smoke


def test_fixed_claim_is_provenance_only():
    assert "not an evaluation result" in smoke.FIXED_CLAIM_TEMPLATE
    assert "model-quality comparison" in smoke.FIXED_CLAIM_TEMPLATE
    assert "Gate 2 repair claim" in smoke.FIXED_CLAIM_TEMPLATE


def test_smoke_uses_the_ultra_request_by_default():
    assert smoke.DEFAULT_MODEL == "nvidia/nemotron-3-ultra-550b-a55b"


def test_claim_template_has_no_backticks_and_script_is_ascii():
    assert "`" not in smoke.FIXED_CLAIM_TEMPLATE
    assert "requested {requested} and was served by {served}." in smoke.FIXED_CLAIM_TEMPLATE
    source = open(smoke.__file__, encoding="utf-8").read()
    assert source.isascii()
    assert "super-vl" not in (smoke.__doc__ or "")


import json
from functools import partial

import httpx

from app.agents.relevance import RelevanceClient

MOCK_SERVED = "nvidia/nemotron-3-super-120b-a12b"


def _offline_client(seen: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        body = {
            "model": MOCK_SERVED,
            "choices": [{"message": {"role": "assistant", "content": '{"ok":true,"purpose":"provenance-smoke"}'}}],
        }
        return httpx.Response(200, json=body)

    return partial(RelevanceClient, token="test-token-not-real", transport=httpx.MockTransport(handler))


def test_main_completes_offline_with_mocked_served_model(tmp_path, monkeypatch, capsys):
    seen: list[dict] = []
    monkeypatch.setattr(smoke, "prepare_cli", lambda: None)
    monkeypatch.setattr(smoke, "trace_secrets", lambda: [])
    monkeypatch.setattr(smoke, "RelevanceClient", _offline_client(seen))
    log_dir = tmp_path / "logs"
    cache_dir = tmp_path / "cache"

    code = smoke.main(["--run-id", "offline-test", "--log-dir", str(log_dir), "--cache-dir", str(cache_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert len(seen) == 1
    assert f"Requested: {smoke.DEFAULT_MODEL}" in out
    assert f"Served: {MOCK_SERVED}" in out
    assert "Policy: require" in out
    assert "Run hash: " in out
    assert smoke.FIXED_CLAIM_TEMPLATE.format(requested=smoke.DEFAULT_MODEL, served=MOCK_SERVED) in out
    trace_file = log_dir / "trace-offline-test.jsonl"
    assert trace_file.is_file()
    lines = [json.loads(line) for line in trace_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    calls = [line for line in lines if line.get("action") == "LLM_CALL"]
    assert len(calls) == 1
    assert calls[0]["served_model"] == MOCK_SERVED
    assert calls[0]["requested_model"] == smoke.DEFAULT_MODEL
    assert list(cache_dir.glob("*.json"))


def test_served_model_reads_the_tuple_and_reports_missing():
    assert smoke._served_model(("text", "req", "served-id")) == "served-id"
    assert smoke._served_model(("text", "req", "")) == "(missing)"
