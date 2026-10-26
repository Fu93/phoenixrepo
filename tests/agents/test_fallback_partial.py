"""The alternatives-phase cap keeps a verified blocker when a later chunk stops.

Replays the Nekmo/telegram-upload trace: chunk 2's batch returned HTTP 502 twice,
per-item fallback then judged TeleCollection, and a later chunk must not throw that
row away. Mock transport only. No live Nightshift call.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, timedelta

import httpx

from app.agents.relevance import (
    FallbackBudget,
    InfraFallbackStop,
    RelevanceClient,
    RelevanceLLMError,
    filter_relevant_alternatives,
    judge_partial_alternatives,
)
from scripts.rerun_unjudged import is_infra_review
from scripts.score_candidates import verdict_for_infra_stop

QUOTE = "directly solves the core problem"
RECENT = (date.today() - timedelta(days=10)).isoformat()
STALE = (date.today() - timedelta(days=800)).isoformat()
CANDIDATE = {
    "repo_id": "Nekmo/telegram-upload",
    "html_url": "https://github.com/Nekmo/telegram-upload",
    "description": "Upload large files to Telegram.",
    "readme_excerpt": "Upload large files to Telegram.",
    "stars": 200,
}
PROBLEM = {
    "problem": "Upload large files to Telegram.",
    "target_user": "people sending folders",
    "category": "telegram",
}


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


def _row(name: str, description: str, **extra) -> dict:
    row = {
        "alternative_id": name,
        "url": f"https://github.com/{name}",
        "description": description,
        "source": "github",
        "stars": extra.pop("stars", 1),
        "pushed_at": extra.pop("pushed_at", STALE),
        "licence_spdx": extra.pop("licence_spdx", "MIT"),
        "archived": False,
    }
    row.update(extra)
    return row


def _rows() -> list[dict]:
    return [
        _row("other/alpha", "Alpha lists local files and does not upload them."),
        _row("other/beta", "Beta renames photos on disk."),
        _row(
            "NeritonDias/telecollection",
            f"TeleCollection {QUOTE} for people uploading a folder into Telegram.",
            stars=800,
            pushed_at=RECENT,
        ),
        _row("other/gamma", "Gamma is a calculator."),
        _row("other/delta", "Delta should stay unjudged."),
        _row("other/epsilon", "Epsilon should stay unjudged."),
        _row("other/zeta", "Zeta should stay unjudged."),
        _row("other/eta", "Eta should stay unjudged."),
    ]


def _not_same(indexes: list[int]) -> str:
    return json.dumps({"results": [
        {"index": i, "same_problem": False, "overlap": "none", "reason": "different job", "quotes": []}
        for i in indexes
    ]})


def _messages(request: httpx.Request) -> str:
    payload = json.loads(request.content.decode())
    return "\n".join(str(item.get("content") or "") for item in payload.get("messages") or [])


def test_chunk_two_batch_502_keeps_telecollection_and_stops_later_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr("app.agents.relevance.RETRY_DELAYS", (0,))
    clock = _Clock()
    seen: list[str] = []
    batch_kinds: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        text = _messages(request)
        seen.append(text)
        if any(name in text for name in ("other/delta", "other/epsilon", "other/zeta", "other/eta")):
            raise AssertionError("a chunk after the 502 cap was sent to the model")
        if "BATCH MODE" in text and "other/alpha" in text:
            clock.now += 500
            return httpx.Response(200, json={
                "model": "served-ok",
                "choices": [{"message": {"content": _not_same([0, 1])}}],
            })
        if "BATCH MODE" in text and "telecollection" in text:
            clock.now += 0.01
            batch_kinds.append("retry" if "Retry:" in text else "first")
            return httpx.Response(502, json={"error": "bad gateway"})
        if "telecollection" in text:
            body = {
                "same_problem": True,
                "overlap": "full",
                "reason": QUOTE,
                "quotes": [QUOTE],
            }
            return httpx.Response(200, json={
                "model": "served-telecollection",
                "choices": [{"message": {"content": json.dumps(body)}}],
            })
        if "other/gamma" in text:
            body = {"same_problem": False, "overlap": "none", "reason": "different", "quotes": []}
            return httpx.Response(200, json={
                "model": "served-gamma",
                "choices": [{"message": {"content": json.dumps(body)}}],
            })
        raise AssertionError(text[:240])

    client = RelevanceClient(
        token="meterless-test-token",
        transport=httpx.MockTransport(handler),
        cache_dir=tmp_path / "cache",
        wall_clock_timeout=30,
        total_timeout=30,
    )
    trace = _Trace()
    budget = FallbackBudget(clock=clock)
    try:
        filter_relevant_alternatives(
            PROBLEM,
            CANDIDATE["repo_id"],
            _rows(),
            client=client,
            trace=trace,
            candidate=CANDIDATE,
            fallback_budget=budget,
        )
    except InfraFallbackStop as exc:
        stopped = exc
    else:
        raise AssertionError("later chunks kept calling after two batch HTTP 502s")

    assert "first" in batch_kinds and "retry" in batch_kinds
    assert stopped.cause == "infra_failures"
    assert stopped.batch_502s == 2
    assert stopped.per_item_502s == 0
    assert stopped.failures == 2
    assert stopped.unjudged_chunks == 2
    assert stopped.completed_calls >= 1
    assert budget.infra_seconds < 10
    reason = str(stopped)
    assert reason.startswith("infra:")
    assert "HTTP 502" in reason
    assert "infrastructure failures" in reason
    assert "0 infrastructure" not in reason
    assert "time cap exceeded" not in reason
    names = {str(row.get("alternative_id")) for row in stopped.verified}
    assert names == {"NeritonDias/telecollection"}
    assert is_infra_review({"verdict": "NEEDS_REVIEW", "reason": reason})

    verified_at = [i for i, event in enumerate(trace.events) if event.get("action") == "ALTERNATIVES_VERIFIED"]
    call_at = [
        i for i, event in enumerate(trace.events)
        if event.get("action") == "ALTERNATIVE_CALL" and "telecollection" in str((event.get("input_summary") or {}).get("alternative"))
    ]
    assert verified_at and call_at and call_at[-1] < verified_at[-1]
    partial = trace.events[verified_at[-1]]
    assert partial["input_summary"]["partial"] is True
    assert partial["output_summary"]["partial"] is True
    assert partial["output_summary"]["unjudged_chunks"] == 2
    tele_call = trace.events[call_at[-1]]["output_summary"]
    assert tele_call["overlap"] == "full"
    assert tele_call["same_problem"] is True
    assert tele_call["quote_verified"] is True

    class _Facts:
        def __init__(self) -> None:
            self.seen: list[str] = []

        def fetch_alternative(self, row: dict) -> dict:
            self.seen.append(str(row.get("alternative_id")))
            return dict(row)

    facts = _Facts()
    judged = verdict_for_infra_stop(CANDIDATE, stopped, facts)
    assert facts.seen == ["NeritonDias/telecollection"]
    assert judged[0] == "ALREADY_EXISTS"
    assert "telecollection" in judged[1].lower()
    assert judged[0] != "CANDIDATE"
    assert not is_infra_review({"verdict": judged[0], "reason": judged[1]})


def test_time_cap_message_counts_calls_and_batch_502s(tmp_path):
    clock = _Clock()

    def llm(prompt: str, system: str) -> str:
        if "BATCH MODE" in system and "other/alpha" in prompt:
            clock.now += 400
            return _not_same([0, 1])
        clock.now += 6
        raise RelevanceLLMError("Nightshift returned HTTP 502")

    client = RelevanceClient(cache_dir=tmp_path / "cap", mock_handler=llm)
    budget = FallbackBudget(seconds=10, clock=clock)
    try:
        filter_relevant_alternatives(
            PROBLEM,
            CANDIDATE["repo_id"],
            _rows()[:4],
            client=client,
            candidate=CANDIDATE,
            fallback_budget=budget,
        )
    except InfraFallbackStop as exc:
        stopped = exc
    else:
        raise AssertionError("the time cap let the batch retry keep running")
    assert stopped.cause == "time_cap"
    text = str(stopped)
    assert text.startswith("infra: time cap exceeded after ")
    assert "completed alternative calls" in text
    assert "batch 502s" in text
    assert "per-item 502s" in text
    assert stopped.batch_502s >= 1
    assert stopped.completed_calls >= 1
    assert budget.infra_seconds >= 10
    assert is_infra_review({"verdict": "NEEDS_REVIEW", "reason": text})


def test_alternatives_budget_bounds_a_hung_batch(tmp_path, monkeypatch):
    monkeypatch.setattr("app.agents.relevance.RETRY_DELAYS", (0, 3))
    release = threading.Event()

    def handler(_request: httpx.Request) -> httpx.Response:
        release.wait(5)
        return httpx.Response(200, json={
            "model": "late-served-model",
            "choices": [{"message": {"content": "{}"}}],
        })

    client = RelevanceClient(
        token="meterless-test-token",
        transport=httpx.MockTransport(handler),
        cache_dir=tmp_path / "hung",
        wall_clock_timeout=30,
        total_timeout=30,
    )
    started = time.monotonic()
    try:
        filter_relevant_alternatives(
            PROBLEM,
            CANDIDATE["repo_id"],
            _rows()[:2],
            client=client,
            candidate=CANDIDATE,
            fallback_budget=FallbackBudget(seconds=1.2),
        )
    except InfraFallbackStop as exc:
        stopped = exc
    else:
        raise AssertionError("a hung batch ran until the client timeout")
    elapsed = time.monotonic() - started
    release.set()
    time.sleep(0.3)
    assert elapsed < 3.5
    assert stopped.cause == "time_cap"
    assert "time cap exceeded after" in str(stopped)
    assert "completed alternative calls" in str(stopped)
    assert "late-served-model" not in client.served_models
    assert "late-served-model" not in client.model_calls


def test_immature_unverified_and_partial_rows_do_not_block():
    mature = {
        "alternative_id": "NeritonDias/telecollection",
        "url": "https://github.com/NeritonDias/telecollection",
        "same_problem": True,
        "overlap": "full",
        "quotes": [QUOTE],
        "quote_verified": True,
        "stars": 800,
        "pushed_at": RECENT,
        "licence_spdx": "MIT",
        "archived": False,
        "source": "github",
    }
    infra = "infra: stopped after 2 infrastructure failures; cause: Nightshift returned HTTP 502"
    blocked = judge_partial_alternatives(
        CANDIDATE, [mature], unjudged_chunks=2, infra_reason=infra, as_of=date.today(),
    )
    assert blocked[0] == "ALREADY_EXISTS"

    immature = dict(mature, stars=3, pushed_at=STALE)
    held = judge_partial_alternatives(
        CANDIDATE, [immature], unjudged_chunks=2, infra_reason=infra, as_of=date.today(),
    )
    assert held[0] == "NEEDS_REVIEW"
    assert held[0] != "CANDIDATE"
    assert "2 chunk(s) unjudged" in held[1]
    assert "NeritonDias/telecollection" in held[1]
    assert is_infra_review({"verdict": held[0], "reason": held[1]})
    assert held.must_beat

    unverified = dict(mature, quote_verified=False, quotes=[])
    review = judge_partial_alternatives(
        CANDIDATE, [unverified], unjudged_chunks=1, infra_reason=infra, as_of=date.today(),
    )
    assert review[0] == "NEEDS_REVIEW"
    assert "quote unverified" in review[1]

    partial = dict(mature, overlap="partial")
    partial_review = judge_partial_alternatives(
        CANDIDATE, [partial], unjudged_chunks=1, infra_reason=infra, as_of=date.today(),
    )
    assert partial_review[0] == "NEEDS_REVIEW"
    assert partial_review[0] != "ALREADY_EXISTS"
