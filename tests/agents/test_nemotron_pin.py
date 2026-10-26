"""Gate-1 verdict calls request Nemotron through Meterless, and a non-Nemotron serve is visible.

Mock transport only. These tests do not call Nightshift.
"""

from __future__ import annotations

import json
import threading
import time

import httpx
import pytest

from app.agents.relevance import InfraFallbackStop, RelevanceClient, filter_relevant_alternatives
from app.trace_log import HackathonTrace
from config.settings import DEFAULT_NIGHTSHIFT_MODEL, Settings
from scripts.evidence_report import render_repo_html, served_model_rows

NEMOTRON = "nvidia/nemotron-3-super-120b-a12b"
ULTRA = "nvidia/nemotron-3-ultra-550b-a55b"
GEMINI = "gemini-3.1-pro-preview"


def _transport(served: str | None) -> tuple[httpx.MockTransport, dict]:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode())
        payload: dict = {"choices": [{"message": {"content": '{"ok": true}'}}]}
        if served is not None:
            payload["model"] = served
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler), seen


def _client(tmp_path, served: str | None, *, model: str | None = None, trace=None) -> tuple[RelevanceClient, dict]:
    transport, seen = _transport(served)
    kwargs = dict(
        token="test-token",
        cache_dir=tmp_path / "cache",
        transport=transport,
        trace=trace,
    )
    if model is not None:
        kwargs["model"] = model
    return RelevanceClient(**kwargs), seen


def _llm_lines(trace: HackathonTrace) -> list[dict]:
    return [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_default_requested_model_is_nemotron(monkeypatch):
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("METERLESS_MODEL", raising=False)
    assert DEFAULT_NIGHTSHIFT_MODEL == NEMOTRON
    assert Settings(_env_file=None).requested_model() == NEMOTRON
    assert RelevanceClient.__dataclass_fields__["model"].default == NEMOTRON


def test_model_name_still_overrides_the_nemotron_default(monkeypatch):
    monkeypatch.delenv("METERLESS_MODEL", raising=False)
    monkeypatch.setenv("MODEL_NAME", "meterless/fast")
    assert Settings(_env_file=None).requested_model() == "meterless/fast"


def test_http_match_keeps_the_served_nemotron_id(tmp_path):
    trace = HackathonTrace("match", tmp_path / "logs")
    client, seen = _client(tmp_path, ULTRA, trace=trace)
    text, requested, served = client.complete("prompt", "system", repo_id="o/r", step="problem")
    assert text == '{"ok": true}'
    assert requested == NEMOTRON
    assert seen["body"]["model"] == NEMOTRON
    assert "integrate.api.nvidia.com" not in seen["url"]
    assert seen["url"].endswith("/chat/completions")
    # Ultra is still the Nemotron family. Do not rewrite it to the id we asked for.
    assert served == ULTRA
    line = _llm_lines(trace)[0]
    assert line["requested_model"] == NEMOTRON
    assert line["served_model"] == ULTRA
    assert "MODEL_MISMATCH" not in (line.get("flags") or [])
    assert client.mismatch_count == 0


def test_http_mismatch_flags_a_non_nemotron_serve(tmp_path):
    trace = HackathonTrace("mismatch", tmp_path / "logs")
    client, seen = _client(tmp_path, GEMINI, trace=trace)
    _, requested, served = client.complete("prompt", "system", repo_id="o/r", step="discovery")
    assert requested == NEMOTRON
    assert seen["body"]["model"] == NEMOTRON
    assert served == GEMINI
    line = _llm_lines(trace)[0]
    assert line["served_model"] == GEMINI
    assert line["flags"] == ["MODEL_MISMATCH"]
    assert client.mismatch_count == 1
    assert "o/r" in client.repo_mismatches


def test_http_missing_served_model_is_not_relabelled(tmp_path):
    trace = HackathonTrace("missing", tmp_path / "logs")
    client, _seen = _client(tmp_path, None, trace=trace)
    _, requested, served = client.complete("prompt", "system", repo_id="o/r", step="problem")
    assert requested == NEMOTRON
    assert served == ""
    assert served != NEMOTRON
    cached = json.loads(next((tmp_path / "cache").glob("*.json")).read_text(encoding="utf-8"))
    assert cached["requested_model"] == NEMOTRON
    assert cached["served_model"] == ""
    line = _llm_lines(trace)[0]
    assert line["requested_model"] == NEMOTRON
    assert line["served_model"] == ""
    assert line["flags"] == ["MODEL_MISMATCH"]
    # A later read of the cache must still say the serve was missing.
    again = client.complete("prompt", "system", repo_id="o/r", step="problem")
    assert again[2] == ""
    assert client.mismatch_count == 2


def test_cache_key_includes_the_requested_model(tmp_path):
    cache = tmp_path / "cache"
    first, seen_a = _client(tmp_path, NEMOTRON, model=NEMOTRON)
    first.cache_dir = cache
    other_transport, seen_b = _transport(GEMINI)
    second = RelevanceClient(
        token="test-token",
        model="other/model",
        cache_dir=cache,
        transport=other_transport,
    )
    text_a, _, served_a = first.complete("same", "sys")
    text_b, requested_b, served_b = second.complete("same", "sys")
    assert seen_a["body"]["model"] == NEMOTRON
    assert seen_b["body"]["model"] == "other/model"
    assert text_a == text_b
    assert served_a == NEMOTRON
    assert served_b == GEMINI
    assert requested_b == "other/model"
    assert len(list(cache.glob("*.json"))) == 2
    # Replaying the first request must not return the second model's serve.
    replay_served = first.complete("same", "sys")[2]
    assert replay_served == NEMOTRON


def test_evidence_report_has_a_per_run_served_model_table(tmp_path):
    trace = HackathonTrace("report", tmp_path / "logs")
    trace.event(
        agent_name="orchestrator",
        action="LLM_CALL",
        llm={"requested_model": NEMOTRON, "served_model": ULTRA},
    )
    trace.event(
        agent_name="orchestrator",
        action="LLM_CALL",
        llm={"requested_model": NEMOTRON, "served_model": GEMINI},
    )
    trace.event(
        agent_name="orchestrator",
        action="LLM_CALL",
        llm={"requested_model": NEMOTRON, "served_model": ""},
    )
    trace.seal()
    events = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines()]
    rows = served_model_rows(events)
    by_model = {row["served_model"]: row for row in rows}
    assert by_model[ULTRA]["calls"] == 1 and by_model[ULTRA]["mismatches"] == 0
    assert by_model[GEMINI]["calls"] == 1 and by_model[GEMINI]["mismatches"] == 1
    assert by_model["(missing)"]["calls"] == 1 and by_model["(missing)"]["mismatches"] == 1
    page = render_repo_html("o/r", "NEEDS_REVIEW", "because", {}, run_id="report", model_rows=rows)
    assert "Served models" in page
    assert ULTRA in page and GEMINI in page and "(missing)" in page
    assert "MODEL_MISMATCH" in page
    assert page.count(GEMINI) >= 1


def test_revive_requests_nemotron_through_meterless_and_honors_model_name(tmp_path, monkeypatch):
    from scripts.revive import build_relevance_client

    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.delenv("METERLESS_MODEL", raising=False)
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "test-token")
    monkeypatch.setenv("METERLESS_BASE_URL", "http://127.0.0.1:8088/v1")
    trace = HackathonTrace("revive", tmp_path / "logs")
    transport, seen = _transport(NEMOTRON)
    client = build_relevance_client(cache_dir=tmp_path / "cache", transport=transport, trace=trace)
    _, requested, served = client.complete("patch", "system", repo_id="o/r", step="revive")
    assert requested == NEMOTRON
    assert served == NEMOTRON
    assert seen["body"]["model"] == NEMOTRON
    assert seen["url"].startswith("http://127.0.0.1:8088/v1/chat/completions")
    assert "integrate.api.nvidia.com" not in seen["url"]
    assert "MODEL_MISMATCH" not in (_llm_lines(trace)[0].get("flags") or [])

    monkeypatch.setenv("MODEL_NAME", "meterless/auto")
    transport_b, seen_b = _transport(GEMINI)
    overridden = build_relevance_client(cache_dir=tmp_path / "cache-b", transport=transport_b, trace=trace)
    _, requested_b, served_b = overridden.complete("patch-2", "system", repo_id="o/r", step="revive")
    assert requested_b == "meterless/auto"
    assert seen_b["body"]["model"] == "meterless/auto"
    assert served_b == GEMINI
    flagged = [line for line in _llm_lines(trace) if line.get("action") == "LLM_CALL"][-1]
    assert flagged["served_model"] == GEMINI
    assert flagged["flags"] == ["MODEL_MISMATCH"]


def test_run_summary_counts_mismatch_flags(tmp_path):
    from scripts.score_candidates import _render_markdown

    data = {
        "date": "2026-10-26",
        "run_hash": "abc",
        "command": "phoenix judge",
        "llm_used": True,
        "model_calls": {ULTRA: 2, GEMINI: 1, "(missing)": 1},
        "model_mismatch_count": 2,
        "warnings": [],
        "protagonist": {"repo_id": None, "reason": "none"},
        "candidates": [],
        "ineligible": [],
    }
    markdown = _render_markdown(data)
    assert "MODEL_MISMATCH" in markdown
    assert "| `gemini-3.1-pro-preview` | 1 |" in markdown or GEMINI in markdown
    assert "**MODEL_MISMATCH**: 2" in markdown or "MODEL_MISMATCH count: 2" in markdown or "| 2 |" in markdown


def test_capped_fallback_flags_mismatch_and_drops_a_late_serve(tmp_path):
    """A call the 120s cap abandons records an empty serve and MODEL_MISMATCH.

    The worker may still return HTTP 200 after the join times out. That late model id
    must not land on the client, the cache, or the trace.
    """

    release = threading.Event()

    def handler(_request: httpx.Request) -> httpx.Response:
        release.wait(2.0)
        return httpx.Response(
            200,
            json={
                "model": "late-served-model",
                "choices": [{"message": {"content": json.dumps({"results": []})}}],
            },
        )

    trace = HackathonTrace("cap", tmp_path / "logs")
    client = RelevanceClient(
        token="meterless-test-token",
        transport=httpx.MockTransport(handler),
        cache_dir=tmp_path / "cache",
        wall_clock_timeout=0.2,
        total_timeout=0.2,
        trace=trace,
    )
    rows = [
        {
            "alternative_id": f"other/tool-{i}",
            "url": f"https://github.com/other/tool-{i}",
            "description": f"Tool {i} does a different job number {i}.",
            "source": "github",
        }
        for i in range(3)
    ]
    problem = {
        "problem": "Generate TypeScript interfaces from pydantic models.",
        "target_user": "Python developers",
        "category": "developer-tools",
    }
    started = time.monotonic()
    try:
        filter_relevant_alternatives(
            problem,
            "phillipdupuis/pydantic-to-typescript",
            rows,
            client=client,
            trace=trace,
            candidate={"repo_id": "phillipdupuis/pydantic-to-typescript"},
        )
    except InfraFallbackStop as exc:
        assert str(exc).startswith("infra:")
    else:
        raise AssertionError("the fallback cap kept waiting after it had given up")
    assert time.monotonic() - started < 3.0
    release.set()
    time.sleep(0.5)
    blob = trace.path.read_text(encoding="utf-8")
    assert "late-served-model" not in blob
    assert "late-served-model" not in client.served_models
    assert "late-served-model" not in client.model_calls
    for path in (tmp_path / "cache").glob("*.json"):
        assert "late-served-model" not in path.read_text(encoding="utf-8")
    lines = [json.loads(line) for line in blob.splitlines() if line.strip()]
    flagged = [line for line in lines if "MODEL_MISMATCH" in (line.get("flags") or [])]
    assert flagged
    assert all(line.get("requested_model") == NEMOTRON for line in flagged)
    assert all(line.get("served_model") == "" for line in flagged)
    assert trace.model_mismatch_count() == len(flagged)
