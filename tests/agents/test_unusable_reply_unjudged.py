"""An unusable per-item alternative reply is unjudged, not "no competitor".

Invalid JSON and a reply missing the required keys used to be dropped while the
chunk still counted as judged, so the repo could finish CANDIDATE. With
PHOENIX_UNUSABLE_REPLY_UNJUDGED on (the default) that reply takes the same stop
as a partial alternatives phase: NEEDS_REVIEW via judge_partial_alternatives,
never CANDIDATE. A failed quote stays a dropped judgement, including when the
quote text contains "timeout" or "429".
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from app.agents.relevance import (
    FallbackBudget,
    InfraFallbackStop,
    RelevanceClient,
    _infra_error,
    filter_relevant_alternatives,
    judge_discovery,
)
from scripts.score_candidates import verdict_for_infra_stop

QUOTE = "directly solves the core problem"
RECENT = (date.today() - timedelta(days=10)).isoformat()
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
_CANDIDATE_VERDICT = {
    "verdict": "CANDIDATE",
    "reason": "No maintained alternative covers the same job.",
    "problem_quote": "Upload large files to Telegram.",
    "alternative_repo": None,
    "alternative_quote": None,
}


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
        "pushed_at": extra.pop("pushed_at", "2020-01-01"),
        "licence_spdx": extra.pop("licence_spdx", "MIT"),
        "archived": False,
    }
    row.update(extra)
    return row


def _clear_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHOENIX_UNUSABLE_REPLY_UNJUDGED", raising=False)


def _run(tmp_path, replies: dict[str, str], rows: list[dict], *, trace: _Trace | None = None, budget: FallbackBudget | None = None):
    def llm(prompt: str, system: str) -> str:
        if "BATCH MODE" in system:
            if "NeritonDias/telecollection" in prompt:
                return json.dumps({"results": [
                    {
                        "index": 0,
                        "same_problem": True,
                        "overlap": "full",
                        "reason": QUOTE,
                        "quotes": [QUOTE],
                    },
                    {"index": 1, "same_problem": False, "overlap": "none", "reason": "different job", "quotes": []},
                ]})
            # A closed first object is kept; the missing index is judged per item.
            if "other/partial-batch" in prompt:
                return json.dumps({"results": [
                    {"index": 0, "same_problem": False, "overlap": "none", "reason": "different job", "quotes": []},
                ]})
            return "not json"
        for name, body in replies.items():
            if name in prompt:
                return body
        raise AssertionError(prompt[:240])

    client = RelevanceClient(cache_dir=tmp_path / "cache", mock_handler=llm)
    return filter_relevant_alternatives(
        PROBLEM,
        CANDIDATE["repo_id"],
        rows,
        client=client,
        trace=trace,
        candidate=CANDIDATE,
        fallback_budget=budget,
    )


def _unusable_events(trace: _Trace) -> list[dict]:
    return [event for event in trace.events if event.get("action") == "UNUSABLE_ALTERNATIVE_REPLY"]


def test_invalid_json_per_item_reply_is_needs_review_not_candidate(tmp_path, monkeypatch):
    _clear_flag(monkeypatch)
    trace = _Trace()
    budget = FallbackBudget()
    rows = [_row("other/alpha", "Alpha lists local files and does not upload them.")]
    try:
        _run(tmp_path, {"other/alpha": "this is not json"}, rows, trace=trace, budget=budget)
    except InfraFallbackStop as exc:
        stopped = exc
    else:
        raise AssertionError("invalid JSON was treated as a finished judgement")

    assert stopped.cause == "unusable_reply"
    assert stopped.unjudged_chunks >= 1
    assert budget.completed_calls == 0
    assert budget.failures == 0
    judged = verdict_for_infra_stop(CANDIDATE, stopped)
    assert judged[0] == "NEEDS_REVIEW"
    assert judged[0] != "CANDIDATE"
    events = _unusable_events(trace)
    assert len(events) == 1
    assert events[0]["input_summary"]["alternative"] == "other/alpha"
    assert events[0]["output_summary"]["alternative"] == "other/alpha"
    assert "invalid JSON" in events[0]["output_summary"]["reason"]
    assert "other/alpha" in str(stopped)
    assert str(stopped).count("cause:") == 1


def test_missing_required_keys_are_unusable(tmp_path, monkeypatch):
    _clear_flag(monkeypatch)
    trace = _Trace()
    rows = [_row("other/beta", "Beta renames photos on disk.")]
    try:
        _run(tmp_path, {"other/beta": json.dumps({"hello": "world"})}, rows, trace=trace)
    except InfraFallbackStop as exc:
        stopped = exc
    else:
        raise AssertionError("a reply missing the judgement keys was treated as no competitor")

    judged = verdict_for_infra_stop(CANDIDATE, stopped)
    assert judged[0] == "NEEDS_REVIEW"
    assert judged[0] != "CANDIDATE"
    events = _unusable_events(trace)
    assert len(events) == 1
    assert events[0]["output_summary"]["alternative"] == "other/beta"
    assert "missing required keys" in events[0]["output_summary"]["reason"]
    assert "same_problem" in events[0]["output_summary"]["reason"]


def test_partial_batch_salvage_with_invalid_json_does_not_count_as_judged(tmp_path, monkeypatch):
    _clear_flag(monkeypatch)
    trace = _Trace()
    rows = [
        _row("other/partial-batch", "Partial batch item is a different job."),
        _row("other/salvage", "Salvage item has no usable reply."),
    ]
    try:
        _run(
            tmp_path,
            {"other/salvage": "still not json"},
            rows,
            trace=trace,
        )
    except InfraFallbackStop as exc:
        stopped = exc
    else:
        raise AssertionError("the chunk counted as judged after an unusable per-item salvage")

    assert stopped.cause == "unusable_reply"
    judged = verdict_for_infra_stop(CANDIDATE, stopped)
    assert judged[0] == "NEEDS_REVIEW"
    assert judged[0] != "CANDIDATE"
    assert any(event["output_summary"]["alternative"] == "other/salvage" for event in _unusable_events(trace))


def test_later_chunk_is_not_sent_after_an_unusable_reply(tmp_path, monkeypatch):
    _clear_flag(monkeypatch)
    seen: list[str] = []

    def llm(prompt: str, system: str) -> str:
        seen.append(prompt)
        if "other/later" in prompt:
            raise AssertionError("a chunk after an unusable reply was sent to the model")
        return "not json"

    client = RelevanceClient(cache_dir=tmp_path / "later", mock_handler=llm)
    rows = [
        _row("other/alpha", "Alpha lists files."),
        _row("other/beta", "Beta renames photos."),
        _row("other/later", "Later should stay unjudged."),
    ]
    try:
        filter_relevant_alternatives(
            PROBLEM, CANDIDATE["repo_id"], rows, client=client, candidate=CANDIDATE,
        )
    except InfraFallbackStop as exc:
        stopped = exc
    else:
        raise AssertionError("the phase continued after an unusable reply")
    assert stopped.unjudged_chunks >= 1
    assert not any("other/later" in prompt for prompt in seen)


@pytest.mark.parametrize("value", ["0", "off", "false", "no", "disabled", "OFF", " False "])
def test_flag_off_keeps_a_dropped_unusable_reply_on_the_candidate_path(tmp_path, monkeypatch, value):
    monkeypatch.setenv("PHOENIX_UNUSABLE_REPLY_UNJUDGED", value)
    trace = _Trace()
    budget = FallbackBudget()
    rows = [_row("other/alpha", "Alpha lists local files and does not upload them.")]
    verified = _run(
        tmp_path,
        {"other/alpha": "this is not json"},
        rows,
        trace=trace,
        budget=budget,
    )
    assert verified == []
    assert budget.failures == 0
    assert budget.completed_calls == 1
    assert _unusable_events(trace) == []
    client = RelevanceClient(
        cache_dir=tmp_path / "discovery",
        mock_handler=lambda prompt, system: json.dumps(_CANDIDATE_VERDICT),
    )
    verdict, _reason = judge_discovery(CANDIDATE, [], [], client=client, problem_alternatives=verified)
    assert verdict == "CANDIDATE"


def test_flag_off_still_drops_a_reply_that_omits_required_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_UNUSABLE_REPLY_UNJUDGED", "0")
    trace = _Trace()
    rows = [_row("other/beta", "Beta renames photos on disk.")]
    verified = _run(tmp_path, {"other/beta": json.dumps({"hello": "world"})}, rows, trace=trace)
    assert verified == []
    assert _unusable_events(trace) == []


def test_quote_containing_timeout_or_429_is_not_an_infra_failure(tmp_path, monkeypatch):
    _clear_flag(monkeypatch)
    quote = "retries on timeout and on HTTP 429"
    body = json.dumps({
        "same_problem": True,
        "overlap": "full",
        "reason": "The text mentions timeout handling.",
        "quotes": [quote],
    })
    trace = _Trace()
    budget = FallbackBudget()
    rows = [_row("other/gamma", "Gamma is a calculator with no network client.")]
    verified = _run(tmp_path, {"other/gamma": body}, rows, trace=trace, budget=budget)
    assert verified == []
    assert budget.failures == 0
    assert budget.per_item_502s == 0
    assert budget.completed_calls == 1
    assert _unusable_events(trace) == []
    embedded = f"unverified LLM quote: '{quote}' not found in alternative text"
    assert not _infra_error(embedded)
    assert any(
        event.get("action") == "UNVERIFIED_ALTERNATIVE_QUOTE"
        and "timeout" in str((event.get("output_summary") or {}).get("error"))
        for event in trace.events
    )


def test_infra_error_matches_known_prefixes_only():
    assert _infra_error("Nightshift returned HTTP 502")
    assert _infra_error("Nightshift returned HTTP 503")
    assert _infra_error("Nightshift returned HTTP 504")
    assert _infra_error("Nightshift returned HTTP 429")
    assert _infra_error("Nightshift request timed out (wall-clock cap of 1.00s exceeded).")
    assert _infra_error("Nightshift call exceeded total timeout limit of 30s across retries.")
    assert _infra_error("Nightshift request failed: The read operation timed out")
    assert not _infra_error("Nightshift returned HTTP 401: Unauthorized")
    assert not _infra_error("LLM output invalid JSON")
    assert not _infra_error("unverified LLM quote: 'timeout' not found in alternative text")
    assert not _infra_error("unverified LLM quote: '429' not found in alternative text")
    assert not _infra_error("a model reason that mentions timeout and HTTP 429 but is a judgement")


def test_verified_blocker_stays_already_exists_when_a_later_reply_is_unusable(tmp_path, monkeypatch):
    _clear_flag(monkeypatch)
    rows = [
        _row(
            "NeritonDias/telecollection",
            f"TeleCollection {QUOTE} for people uploading a folder into Telegram.",
            stars=800,
            pushed_at=RECENT,
        ),
        _row("other/beta", "Beta renames photos."),
        _row("other/gamma", "Gamma is a calculator."),
    ]
    try:
        _run(tmp_path, {"other/gamma": "not json"}, rows)
    except InfraFallbackStop as exc:
        stopped = exc
    else:
        raise AssertionError("the unusable chunk did not stop the phase")
    judged = verdict_for_infra_stop(CANDIDATE, stopped)
    assert judged[0] == "ALREADY_EXISTS"
    assert judged[0] != "CANDIDATE"
    assert "telecollection" in judged[1].lower()
