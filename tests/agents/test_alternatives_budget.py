"""The alternatives budget scales with the chunks, and a judged chunk is cached.

Mock transport and mock handler only. No live Nightshift call.
"""
from __future__ import annotations

import json
import re
import time

import httpx

from app.agents.relevance import (
    ALTERNATIVE_CHUNK_SIZE,
    FallbackBudget,
    InfraFallbackStop,
    RelevanceClient,
    RelevanceLLMError,
    alternatives_budget_seconds,
    filter_relevant_alternatives,
)


def _clear_budget_env(monkeypatch) -> None:
    for name in (
        "PHOENIX_ALTERNATIVES_BUDGET_S",
        "PHOENIX_ALTERNATIVES_BUDGET_FLOOR_S",
        "PHOENIX_ALTERNATIVES_CHUNK_SECONDS",
        "PHOENIX_ALTERNATIVES_BUDGET_CAP_S",
    ):
        monkeypatch.delenv(name, raising=False)


def test_budget_is_max_of_floor_and_per_chunk_then_capped(monkeypatch):
    _clear_budget_env(monkeypatch)
    assert alternatives_budget_seconds(1) == 600
    assert alternatives_budget_seconds(4) == 600
    assert alternatives_budget_seconds(8) == 720
    assert alternatives_budget_seconds(100) == 3600


def test_budget_env_overrides_floor_chunk_seconds_and_cap(monkeypatch):
    _clear_budget_env(monkeypatch)
    monkeypatch.setenv("PHOENIX_ALTERNATIVES_BUDGET_S", "90")
    assert alternatives_budget_seconds(20) == 90
    monkeypatch.delenv("PHOENIX_ALTERNATIVES_BUDGET_S")
    monkeypatch.setenv("PHOENIX_ALTERNATIVES_BUDGET_FLOOR_S", "100")
    monkeypatch.setenv("PHOENIX_ALTERNATIVES_CHUNK_SECONDS", "10")
    assert alternatives_budget_seconds(3) == 100
    assert alternatives_budget_seconds(20) == 200
    monkeypatch.setenv("PHOENIX_ALTERNATIVES_BUDGET_CAP_S", "150")
    assert alternatives_budget_seconds(20) == 150
    monkeypatch.setenv("PHOENIX_ALTERNATIVES_BUDGET_S", "5000")
    assert alternatives_budget_seconds(1) == 150


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _Trace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def event(self, **kwargs) -> None:
        self.events.append(kwargs)


def _rows() -> list[dict]:
    names = ("other/alpha", "other/beta", "other/gamma", "other/delta")
    blurbs = (
        "Alpha lists files.",
        "Beta renames photos.",
        "Gamma is a calculator.",
        "Delta stays unjudged.",
    )
    return [
        {
            "alternative_id": name,
            "url": f"https://github.com/{name}",
            "description": blurb,
            "source": "github",
        }
        for name, blurb in zip(names, blurbs)
    ]


def test_filter_uses_the_env_budget_and_keeps_an_explicit_one(tmp_path, monkeypatch):
    monkeypatch.setattr("app.agents.relevance.RETRY_DELAYS", (0,))
    monkeypatch.setenv("PHOENIX_ALTERNATIVES_BUDGET_S", "5")
    clock = _Clock()

    def llm(prompt: str, _system: str) -> str:
        clock.now += 4
        raise RelevanceLLMError("Nightshift returned HTTP 502")

    problem = {"problem": "Upload files.", "target_user": "people", "category": "files"}
    rows = _rows()[:ALTERNATIVE_CHUNK_SIZE]
    client = RelevanceClient(cache_dir=tmp_path / "env", mock_handler=llm)
    try:
        filter_relevant_alternatives(
            problem, "acme/widget", rows, client=client, candidate={"repo_id": "acme/widget"}, clock=clock,
        )
    except InfraFallbackStop as exc:
        stopped = exc
    else:
        raise AssertionError("the env budget did not stop the chunk")
    assert stopped.cause == "time_cap"
    assert "time cap exceeded" in str(stopped)

    clock = _Clock()
    explicit = FallbackBudget(seconds=100, clock=clock)
    client = RelevanceClient(cache_dir=tmp_path / "explicit", mock_handler=llm)
    try:
        filter_relevant_alternatives(
            problem, "acme/widget", rows, client=client, candidate={"repo_id": "acme/widget"},
            fallback_budget=explicit,
        )
    except InfraFallbackStop as exc:
        stopped = exc
    else:
        raise AssertionError("explicit budget still has an infrastructure limit")
    assert stopped.cause == "infra_failures"
    assert "time cap exceeded" not in str(stopped)
    assert explicit.seconds == 100


def test_call_budget_lets_a_slow_call_finish_and_a_short_budget_stays_short(tmp_path):
    def slow(request: httpx.Request) -> httpx.Response:
        time.sleep(0.25)
        return httpx.Response(200, json={"model": "served-slow", "choices": [{"message": {"content": "ok"}}]})

    client = RelevanceClient(
        token="meterless-test-token",
        cache_dir=tmp_path / "slow",
        wall_clock_timeout=0.05,
        total_timeout=2.0,
        transport=httpx.MockTransport(slow),
    )
    text, _requested, served = client.complete("prompt", "system", call_budget_s=2.0)
    assert text == "ok"
    assert served == "served-slow"

    def slower(request: httpx.Request) -> httpx.Response:
        time.sleep(0.3)
        return httpx.Response(200, json={"model": "served-late", "choices": [{"message": {"content": "late"}}]})

    short = RelevanceClient(
        token="meterless-test-token",
        cache_dir=tmp_path / "short",
        wall_clock_timeout=5.0,
        total_timeout=5.0,
        transport=httpx.MockTransport(slower),
    )
    try:
        short.complete("other", "system", call_budget_s=0.05)
    except RelevanceLLMError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("a short call budget was raised to the wall clock")


def test_rerun_reuses_a_cached_batch_chunk_for_the_same_model(tmp_path, monkeypatch):
    monkeypatch.setattr("app.agents.relevance.RETRY_DELAYS", (0,))
    calls: list[str] = []

    def llm(prompt: str, _system: str) -> str:
        calls.append(prompt)
        if "other/gamma" in prompt or "other/delta" in prompt:
            raise RelevanceLLMError("Nightshift returned HTTP 502")
        indexes = [int(match) for match in re.findall(r"^\[(\d+)\] Alternative", prompt, flags=re.M)]
        return json.dumps({"results": [
            {"index": index, "same_problem": False, "overlap": "none", "reason": "no", "quotes": []}
            for index in indexes
        ]})

    cache = tmp_path / "cache"
    model = "nvidia/nemotron-3-super-120b-a12b"
    client = RelevanceClient(
        model=model,
        cache_dir=cache,
        mock_handler=llm,
        token="meterless-test-token",
    )
    problem = {"problem": "Upload files.", "target_user": "people", "category": "files"}
    trace = _Trace()
    try:
        filter_relevant_alternatives(
            problem, "acme/widget", _rows(), client=client, trace=trace, candidate={"repo_id": "acme/widget"},
        )
    except InfraFallbackStop as exc:
        stopped = exc
    else:
        raise AssertionError("the unjudged chunk did not stop the phase")
    assert stopped.verified_chunk_indexes == [0]
    partial = [event for event in trace.events if event.get("action") == "ALTERNATIVES_VERIFIED"][-1]
    assert partial["output_summary"]["verified_chunk_indexes"] == [0]
    assert partial["output_summary"]["unjudged_chunks"] >= 1
    batch_lines = [event for event in trace.events if event.get("action") == "ALTERNATIVE_BATCH_CALL"]
    assert any((event.get("output_summary") or {}).get("chunk_index") == 0 for event in batch_lines)
    files = list(cache.glob("*.json"))
    assert len(files) == 1
    cached = json.loads(files[0].read_text(encoding="utf-8"))
    assert cached["model"] == model
    assert cached["requested_model"] == model
    assert "meterless-test-token" not in files[0].read_text(encoding="utf-8")
    assert any("other/alpha" in prompt and "other/gamma" not in prompt for prompt in calls)

    calls.clear()
    try:
        filter_relevant_alternatives(
            problem, "acme/widget", _rows(), client=client, candidate={"repo_id": "acme/widget"},
        )
    except InfraFallbackStop:
        pass
    else:
        raise AssertionError("the rerun should still stop on the chunk that was not cached")
    assert calls
    assert all("other/gamma" in prompt or "other/delta" in prompt for prompt in calls)

    other_calls: list[str] = []

    def other_llm(prompt: str, _system: str) -> str:
        other_calls.append(prompt)
        indexes = [int(match) for match in re.findall(r"^\[(\d+)\] Alternative", prompt, flags=re.M)]
        return json.dumps({"results": [
            {"index": index, "same_problem": False, "overlap": "none", "reason": "no", "quotes": []}
            for index in indexes
        ]})

    other = RelevanceClient(model="other/model", cache_dir=cache, mock_handler=other_llm)
    filter_relevant_alternatives(
        problem, "acme/widget", _rows(), client=other, candidate={"repo_id": "acme/widget"},
    )
    assert any("other/alpha" in prompt for prompt in other_calls)
    models = {json.loads(path.read_text(encoding="utf-8"))["model"] for path in cache.glob("*.json")}
    assert model in models
    assert "other/model" in models
