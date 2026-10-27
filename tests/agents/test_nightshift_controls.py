"""Nightshift client controls: env timeouts, extra body, routing provenance, and the probe."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import httpx
import pytest

from app.agents.relevance import (
    DEFAULT_ATTEMPT_TIMEOUT_S,
    DEFAULT_TOTAL_TIMEOUT_S,
    RelevanceClient,
    routing_meta,
)
from app.console import ensure_utf8_stdio
from scripts import nightshift_probe as probe


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    for name in ("PHOENIX_LLM_ATTEMPT_TIMEOUT_S", "PHOENIX_LLM_TOTAL_TIMEOUT_S", "PHOENIX_LLM_MAX_TOKENS", "PHOENIX_LLM_EXTRA_BODY"):
        monkeypatch.delenv(name, raising=False)


def _client(tmp_path: Path, handler, **kwargs) -> RelevanceClient:
    return RelevanceClient(
        base_url="http://nightshift.test/v1",
        token="real-token",
        cache_dir=tmp_path,
        transport=httpx.MockTransport(handler),
        **kwargs,
    )


def test_env_overrides_defaults_but_not_explicit_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PHOENIX_LLM_ATTEMPT_TIMEOUT_S", "45")
    monkeypatch.setenv("PHOENIX_LLM_TOTAL_TIMEOUT_S", "120")
    monkeypatch.setenv("PHOENIX_LLM_MAX_TOKENS", "2048")
    default = RelevanceClient(cache_dir=tmp_path)
    assert (default.wall_clock_timeout, default.timeout, default.total_timeout, default.max_tokens) == (45.0, 45.0, 120.0, 2048)
    explicit = RelevanceClient(cache_dir=tmp_path, wall_clock_timeout=5.0, total_timeout=9.0)
    assert (explicit.wall_clock_timeout, explicit.total_timeout) == (5.0, 9.0)


def test_defaults_without_env(tmp_path: Path):
    client = RelevanceClient(cache_dir=tmp_path)
    assert client.wall_clock_timeout == DEFAULT_ATTEMPT_TIMEOUT_S
    assert client.total_timeout == DEFAULT_TOTAL_TIMEOUT_S
    assert client.extra_body == {}


def test_extra_body_is_sent_and_cannot_override_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "PHOENIX_LLM_EXTRA_BODY",
        json.dumps({"chat_template_kwargs": {"enable_thinking": False}, "model": "gemini-3.1-flash-lite"}),
    )
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"model": "nvidia/nemotron-3.5-lightning-30b-a3b", "choices": [{"message": {"content": "{}"}}]})

    client = _client(tmp_path, handler, max_tokens=1500)
    client.complete("hi", repo_id="o/r", step="problem")
    assert seen["chat_template_kwargs"] == {"enable_thinking": False}
    assert seen["model"] == client.model
    assert seen["max_tokens"] == 1500


def test_invalid_extra_body_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PHOENIX_LLM_EXTRA_BODY", "{not json")
    assert RelevanceClient(cache_dir=tmp_path).extra_body == {}
    monkeypatch.setenv("PHOENIX_LLM_EXTRA_BODY", "[1, 2]")
    assert RelevanceClient(cache_dir=tmp_path).extra_body == {}


def test_routing_meta_reads_body_block_and_headers():
    data = {"model": "gemini-3.1-flash-lite", "meterless": {"routed_model": "gemini-3.1-flash-lite", "winning_route": {"provider": "google"}}}
    meta = routing_meta(data, httpx.Headers({"meterless-route": "fast", "x-other": "1"}))
    assert meta == {"routed_model": "gemini-3.1-flash-lite", "winning_provider": "google", "meterless-route": "fast"}
    assert routing_meta({"model": "x"}) == {}


class _Trace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def event(self, **kwargs):
        self.events.append(kwargs)


def test_routing_is_cached_and_traced(tmp_path: Path):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={"model": "gemini-3.1-flash-lite", "meterless": {"routed_model": "gemini-3.1-flash-lite"}, "choices": [{"message": {"content": "{}"}}]},
        )

    trace = _Trace()
    client = _client(tmp_path, handler, trace=trace)
    client.complete("same prompt", repo_id="o/r", step="problem")
    client.complete("same prompt", repo_id="o/r", step="problem")
    assert calls["n"] == 1  # second call is a cache hit
    routed = [e["output_summary"].get("routing") for e in trace.events]
    assert routed == [{"routed_model": "gemini-3.1-flash-lite"}] * 2
    assert client.mismatch_count == 2


def test_probe_orders_listed_nemotron_ids():
    listed = ["gemini-3.1-flash-lite", "nvidia/nemotron-3-ultra-550b-a55b", "nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia/nemotron-x"]
    assert probe.order_candidates(listed) == [
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/nemotron-x",
    ]


def _probe_transport(served_by_variant: dict[str, str], status_by_variant: dict[str, int] | None = None):
    status_by_variant = status_by_variant or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "nvidia/nemotron-3.5-lightning-30b-a3b"}, {"id": "gemini-3.1-flash-lite"}]})
        body = json.loads(request.content)
        variant = "default"
        if "chat_template_kwargs" in body:
            variant = "thinking_off_template"
        elif "reasoning_effort" in body:
            variant = "reasoning_none"
        status = status_by_variant.get(variant, 200)
        if status != 200:
            return httpx.Response(status, text='{"error":"_empty_response"}')
        return httpx.Response(200, json={"model": served_by_variant[variant], "choices": [{"message": {"content": '{"ok": true, "task": "phoenix-probe"}'}}]})

    return httpx.MockTransport(handler)


def test_probe_recommends_only_a_variant_served_by_nemotron(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "real-token")
    transport = _probe_transport(
        {"default": "gemini-3.1-flash-lite", "thinking_off_template": "nvidia/nemotron-3.5-lightning-30b-a3b", "reasoning_none": "gemini-3.1-flash-lite"}
    )
    real_client = httpx.Client
    monkeypatch.setattr(probe.httpx, "Client", lambda *a, **k: real_client(*a, transport=transport, **k))
    out = tmp_path / "probe.json"
    assert probe.main(["--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    env = report["recommended_env"]
    assert env["MODEL_NAME"] == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert json.loads(env["PHOENIX_LLM_EXTRA_BODY"]) == {"chat_template_kwargs": {"enable_thinking": False}}
    assert int(env["PHOENIX_LLM_ATTEMPT_TIMEOUT_S"]) >= 60
    printed = capsys.readouterr().out
    assert "real-token" not in printed and "real-token" not in out.read_text(encoding="utf-8")
    assert "MISMATCH" in printed


def test_probe_reports_failure_when_nothing_is_nemotron(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "real-token")
    transport = _probe_transport({"default": "gemini-3.1-flash-lite"}, {"thinking_off_template": 502, "reasoning_none": 400})
    real_client = httpx.Client
    monkeypatch.setattr(probe.httpx, "Client", lambda *a, **k: real_client(*a, transport=transport, **k))
    out = tmp_path / "probe.json"
    assert probe.main(["--out", str(out)]) == 1
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["recommended_env"] is None
    statuses = sorted(a["status"] for a in report["attempts"])
    assert statuses == [200, 400, 502]


def test_public_base_drops_userinfo_and_query():
    assert probe.public_base("https://user:real-token@nightshift.example/v1?account=acct-9") == "https://nightshift.example/v1"


def test_redact_drops_routing_headers_and_the_response_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "real-token")
    monkeypatch.setenv("METERLESS_BASE_URL", "https://user:real-token@nightshift.example/v1")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "nvidia/nemotron-3.5-lightning-30b-a3b"}]})
        return httpx.Response(
            200,
            headers={"meterless-account-id": "acct-9", "meterless-route": "fast"},
            json={
                "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
                "choices": [{"message": {"content": '{"ok": true, "task": "phoenix-probe"}'}}],
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(probe.httpx, "Client", lambda *a, **k: real_client(*a, transport=httpx.MockTransport(handler), **k))
    plain = tmp_path / "plain.json"
    redacted = tmp_path / "redacted.json"
    assert probe.main(["--quick", "--out", str(plain)]) == 0
    assert probe.main(["--quick", "--redact", "--out", str(redacted)]) == 0
    captured = capsys.readouterr()
    printed = captured.out + captured.err
    for secret in ("real-token", "acct-9", "meterless-account-id", "user:real-token"):
        assert secret not in printed
    plain_text = plain.read_text(encoding="utf-8")
    assert "meterless-account-id" in plain_text  # local logs/ file keeps routing; it is not for git
    redacted_text = redacted.read_text(encoding="utf-8")
    report = json.loads(redacted_text)
    assert report["redacted"] is True
    assert report["base_url"] == "https://nightshift.example/v1"
    assert "routing" not in json.dumps(report["attempts"])
    for secret in ("real-token", "acct-9", "meterless-account-id", "authorization"):
        assert secret not in redacted_text


def test_probe_without_token_is_a_usage_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "")
    monkeypatch.chdir(Path(probe.__file__).resolve().parent)  # no .env here
    assert probe.main(["--quick"]) == 2


def test_ensure_utf8_stdio_switches_a_legacy_code_page(monkeypatch: pytest.MonkeyPatch):
    raw = io.BytesIO()
    # newline="\n": on Windows the default writes "\r\n", which is not what this test is about.
    legacy = io.TextIOWrapper(raw, encoding="cp950", errors="strict", newline="\n")
    monkeypatch.setattr(sys, "stdout", legacy)
    ensure_utf8_stdio()
    sys.stdout.write("引文 ✓ quote\n")
    sys.stdout.flush()
    assert raw.getvalue().decode("utf-8") == "引文 ✓ quote\n"


def test_probe_recommends_a_deep_model_when_a_larger_nemotron_answered():
    attempts = [
        probe.Attempt("nvidia/nemotron-3.5-lightning-30b-a3b", "thinking_off_template", 200, 3.0, "nvidia/nemotron-3.5-lightning-30b-a3b", True, True),
        probe.Attempt("nvidia/nemotron-3.5-lightning-30b-a3b", "default", 200, 9.0, "nvidia/nemotron-3.5-lightning-30b-a3b", True, True),
        probe.Attempt("nvidia/nemotron-3-ultra-550b-a55b", "default", 200, 20.0, "nvidia/nemotron-3-ultra-550b-a55b", True, True),
        probe.Attempt("nvidia/nemotron-3-super-120b-a12b", "default", 200, 15.0, "gemini-3.1-flash-lite", False, True),
    ]
    env = probe.recommend(attempts)
    assert env["MODEL_NAME"] == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert env["MODEL_NAME_DEEP"] == "nvidia/nemotron-3-ultra-550b-a55b"
    assert int(env["PHOENIX_LLM_ATTEMPT_TIMEOUT_S"]) >= 120  # sized for the slower deep model


def test_prepare_cli_loads_dotenv_without_overriding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.console import load_env_file

    (tmp_path / ".env").write_text("PHOENIX_LLM_TOTAL_TIMEOUT_S=150\nPHOENIX_DEEP_MODEL=from-file\n", encoding="utf-8")
    monkeypatch.delenv("PHOENIX_LLM_TOTAL_TIMEOUT_S", raising=False)
    monkeypatch.setenv("PHOENIX_DEEP_MODEL", "from-process")
    monkeypatch.chdir(tmp_path)
    load_env_file()
    import os

    assert os.environ["PHOENIX_LLM_TOTAL_TIMEOUT_S"] == "150"
    assert os.environ["PHOENIX_DEEP_MODEL"] == "from-process"
    monkeypatch.delenv("PHOENIX_LLM_TOTAL_TIMEOUT_S", raising=False)
