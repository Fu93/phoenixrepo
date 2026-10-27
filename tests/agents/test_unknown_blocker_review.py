"""Rule B holds a CANDIDATE when a full-overlap web alternative was not checked.

The hold is a safety mechanism. It does not change a blocker verdict, ALREADY_EXISTS,
or a row that already has a proven non-match. No network and no model calls.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from app.agents.blocker import evaluate_blocker
from app.agents.relevance import RelevanceClient, judge_discovery

AS_OF = date(2026, 10, 27)
RECENT = (date.today() - timedelta(days=10)).isoformat()
QUOTE = "Sync notes between two folders on your own site."
CANDIDATE = {
    "repo_id": "acme/widget",
    "description": "A small tool that syncs notes between two folders.",
    "readme_excerpt": "A small tool that syncs notes between two folders.",
    "stars": 20,
    "free": True,
    "self_hostable": True,
}
PROBLEM = "A small tool that syncs notes between two folders."


class _Trace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def event(self, **kwargs) -> None:
        self.events.append(kwargs)


def _client(tmp_path, payload: dict):
    return RelevanceClient(cache_dir=tmp_path, mock_handler=lambda _prompt, _sys: json.dumps(payload))


def _says(verdict: str, **extra) -> dict:
    body = {
        "verdict": verdict,
        "reason": "recorded reason",
        "problem_quote": PROBLEM,
        "alternative_repo": extra.pop("alternative_repo", None),
        "alternative_quote": extra.pop("alternative_quote", None),
    }
    body.update(extra)
    return body


def _web(**extra) -> dict:
    row = {
        "alternative_id": "web:https://example.com/folder-sync",
        "url": "https://example.com/folder-sync",
        "source": "web",
        "same_problem": True,
        "same_job": True,
        "overlap": "full",
        "description": QUOTE,
        "excerpt": QUOTE,
        "quotes": [QUOTE],
        "quote_verified": True,
        "stars": 0,
        "pushed_at": "",
        "released_at": "",
        "archived": False,
        "licence_spdx": "",
        "page_kind": "product",
    }
    row.update(extra)
    return row


def _judge(tmp_path, row: dict, *, verdict: str = "CANDIDATE", trace=None, prior=None, **llm):
    rows = prior if prior is not None else [row]
    return judge_discovery(
        CANDIDATE,
        prior_rows=rows,
        demand_rows=[],
        client=_client(tmp_path, _says(verdict, **llm)),
        trace=trace,
    )


def test_unknown_only_full_web_row_needs_review(tmp_path):
    trace = _Trace()
    judged = _judge(tmp_path, _web(), trace=trace)
    assert judged[0] == "NEEDS_REVIEW"
    assert judged[0] != "NEEDS_PRIOR_ART"
    assert judged[0] != "ALREADY_EXISTS"
    assert "UNKNOWN_BLOCKER_FACTS: web:https://example.com/folder-sync full overlap, push/release date, stars, licence/free tier not established" in judged[1]
    assert judged.unknown_blocker["fired"] is True
    assert judged.unknown_blocker["alternative"] == "web:https://example.com/folder-sync"
    assert judged.unknown_blocker["fields"] == ["push/release date", "stars", "licence/free tier"]
    events = [event for event in trace.events if event.get("action") == "UNKNOWN_BLOCKER_FACTS"]
    assert len(events) == 1
    assert events[0]["output_summary"]["fired"] is True
    assert events[0]["output_summary"]["alternative"] == "web:https://example.com/folder-sync"
    assert events[0]["output_summary"]["fields"] == ["push/release date", "stars", "licence/free tier"]
    assert events[0]["input_summary"]["rule"] == "PHOENIX_UNKNOWN_BLOCKER_REVIEW"


def test_unknown_web_row_lists_only_unknown_reasons():
    decision = evaluate_blocker(CANDIDATE, _web(), as_of=AS_OF)
    assert decision["blocks"] is False
    assert decision["verdict_if_blocked"] is None
    assert decision["role"] == "must_beat"
    assert decision["proven_reasons"] == []
    assert decision["maintenance_date_unknown"] is True
    assert decision["unknown_reasons"] == ["push/release date", "stars", "licence/free tier"]
    assert "stars below bar" not in decision["proven_reasons"]


@pytest.mark.parametrize(
    "row",
    [
        _web(pushed_at="2020-01-01"),
        _web(archived=True),
        _web(page_kind="listicle"),
        _web(overlap="partial"),
        _web(same_job=False),
        _web(
            description="Notes sync is paid-only and requires a paid plan.", excerpt="Notes sync is paid-only and requires a paid plan.", quotes=["Notes sync is paid-only and requires a paid plan."]
        ),
    ],
    ids=["stale-with-date", "archived", "listicle", "partial-overlap", "different-job", "paid-only"],
)
def test_proven_non_match_stays_candidate(tmp_path, row):
    judged = _judge(tmp_path, row)
    assert judged[0] == "CANDIDATE"
    assert "UNKNOWN_BLOCKER_FACTS" not in judged[1]
    assert judged.unknown_blocker is None


def test_blocker_classifies_proven_non_matches_without_changing_the_verdict():
    stale = evaluate_blocker(CANDIDATE, _web(pushed_at="2020-01-01"), as_of=AS_OF)
    assert stale["blocks"] is False
    assert "stale" in stale["proven_reasons"]
    assert "stars" in stale["unknown_reasons"]
    assert "push/release date" not in stale["unknown_reasons"]

    archived = evaluate_blocker(CANDIDATE, _web(archived=True), as_of=AS_OF)
    assert archived["blocks"] is False
    assert "archived" in archived["proven_reasons"]
    assert "stale" not in archived["proven_reasons"]

    listicle = evaluate_blocker(CANDIDATE, _web(page_kind="listicle"), as_of=AS_OF)
    assert listicle["blocks"] is False
    assert "page kind listicle" in listicle["proven_reasons"]

    partial = evaluate_blocker(CANDIDATE, _web(overlap="partial"), as_of=AS_OF)
    assert partial["blocks"] is False
    assert "partial overlap" in partial["proven_reasons"]

    different = evaluate_blocker(CANDIDATE, _web(same_job=False), as_of=AS_OF)
    assert different["blocks"] is False
    assert "different job" in different["proven_reasons"]

    paid = _web(
        description="Notes sync is paid-only and requires a paid plan.",
        excerpt="Notes sync is paid-only and requires a paid plan.",
        quotes=["Notes sync is paid-only and requires a paid plan."],
    )
    paid_decision = evaluate_blocker(CANDIDATE, paid, as_of=AS_OF)
    assert paid_decision["blocks"] is False
    assert "paid-only" in paid_decision["proven_reasons"]

    low_stars = evaluate_blocker(
        CANDIDATE,
        {
            "alternative_id": "other/notes",
            "url": "https://github.com/other/notes",
            "source": "github",
            "same_problem": True,
            "same_job": True,
            "overlap": "full",
            "stars": 12,
            "pushed_at": RECENT,
            "licence_spdx": "MIT",
            "page_kind": "product",
            "archived": False,
        },
        as_of=date.today(),
    )
    assert low_stars["blocks"] is False
    assert "stars below bar" in low_stars["proven_reasons"]
    assert "stars" not in low_stars["unknown_reasons"]


def test_mixed_proven_and_unknown_stays_candidate(tmp_path):
    row = _web(pushed_at="2020-01-01")
    decision = evaluate_blocker(CANDIDATE, row, as_of=AS_OF)
    assert decision["blocks"] is False
    assert "stale" in decision["proven_reasons"]
    assert "stars" in decision["unknown_reasons"]
    assert "licence/free tier" in decision["unknown_reasons"]
    judged = _judge(tmp_path, row)
    assert judged[0] == "CANDIDATE"
    assert judged.unknown_blocker is None


def test_switch_off_restores_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_UNKNOWN_BLOCKER_REVIEW", "0")
    trace = _Trace()
    judged = _judge(tmp_path, _web(), trace=trace)
    assert judged[0] == "CANDIDATE"
    assert "UNKNOWN_BLOCKER_FACTS" not in judged[1]
    assert judged.unknown_blocker is None
    assert not [event for event in trace.events if event.get("action") == "UNKNOWN_BLOCKER_FACTS"]


@pytest.mark.parametrize(
    "row",
    [
        {
            "alternative_id": "other/notes-sync",
            "url": "https://github.com/other/notes-sync",
            "source": "github",
            "same_problem": True,
            "same_job": True,
            "overlap": "full",
            "description": QUOTE,
            "excerpt": QUOTE,
            "quotes": [QUOTE],
            "quote_verified": True,
            "stars": None,
            "pushed_at": "",
            "archived": False,
            "licence_spdx": "",
            "page_kind": "product",
            "fetch_status": "not_fetched",
        },
        {
            "alternative_id": "web:https://github.com/other/notes-sync",
            "url": "https://github.com/other/notes-sync",
            "source": "web",
            "same_problem": True,
            "same_job": True,
            "overlap": "full",
            "description": QUOTE,
            "excerpt": QUOTE,
            "quotes": [QUOTE],
            "quote_verified": True,
            "stars": 0,
            "pushed_at": "",
            "archived": False,
            "licence_spdx": "",
            "page_kind": "product",
        },
        {
            "alternative_id": "web:https://gitlab.com/other/notes-sync",
            "url": "https://gitlab.com/other/notes-sync",
            "source": "web",
            "same_problem": True,
            "same_job": True,
            "overlap": "full",
            "description": QUOTE,
            "excerpt": QUOTE,
            "quotes": [QUOTE],
            "quote_verified": True,
            "stars": 0,
            "pushed_at": "",
            "archived": False,
            "licence_spdx": "",
            "page_kind": "product",
        },
    ],
    ids=["github-api-unknown-stars", "web-hit-of-github", "web-hit-of-gitlab"],
)
def test_forge_rows_with_unknown_stars_are_not_downgraded(tmp_path, row):
    judged = _judge(tmp_path, row)
    assert judged[0] == "CANDIDATE"
    assert judged[0] != "NEEDS_REVIEW"
    assert "UNKNOWN_BLOCKER_FACTS" not in judged[1]


def test_unverified_quote_does_not_downgrade(tmp_path):
    row = _web(quotes=["this sentence is not on the page"], quote_verified=False, description="A different product page.", excerpt="A different product page.")
    judged = _judge(tmp_path, row)
    assert judged[0] == "CANDIDATE"


def test_must_beat_path_is_held_for_review(tmp_path):
    judged = _judge(
        tmp_path,
        _web(),
        verdict="ALREADY_EXISTS",
        alternative_repo="web:https://example.com/folder-sync",
        alternative_quote=QUOTE,
    )
    assert judged[0] == "NEEDS_REVIEW"
    assert "UNKNOWN_BLOCKER_FACTS" in judged[1]
    assert judged.must_beat
    assert "did not block" in judged.must_beat[0]["reason"]


def test_own_identity_alone_stays_candidate(tmp_path):
    own = {
        "alternative_id": "acme/widget",
        "url": "https://github.com/acme/widget",
        "same_problem": True,
        "overlap": "full",
        "description": PROBLEM,
        "quotes": [PROBLEM],
        "quote_verified": True,
    }
    judged = _judge(tmp_path, own, verdict="ALREADY_EXISTS", alternative_repo="acme/widget", alternative_quote=PROBLEM)
    assert judged[0] == "CANDIDATE"
    assert "ignored own identity" in judged[1]
    assert judged.unknown_blocker is None


def test_own_identity_path_still_applies_rule_b(tmp_path):
    own = {
        "alternative_id": "acme/widget",
        "url": "https://github.com/acme/widget",
        "same_problem": True,
        "overlap": "full",
        "description": PROBLEM,
        "quotes": [PROBLEM],
    }
    judged = _judge(
        tmp_path,
        _web(),
        verdict="ALREADY_EXISTS",
        alternative_repo="acme/widget",
        alternative_quote=PROBLEM,
        prior=[own, _web()],
    )
    assert judged[0] == "NEEDS_REVIEW"
    assert "UNKNOWN_BLOCKER_FACTS: web:https://example.com/folder-sync" in judged[1]


def _mature() -> dict:
    return {
        "alternative_id": "erudika/para",
        "url": "https://github.com/erudika/para",
        "source": "github",
        "same_problem": True,
        "same_job": True,
        "overlap": "full",
        "description": "Para is a multipurpose backend for object persistence and auth.",
        "excerpt": "Para is a multipurpose backend for object persistence and auth.",
        "quotes": ["a multipurpose backend"],
        "quote_verified": True,
        "stars": 1500,
        "pushed_at": RECENT,
        "archived": False,
        "licence_spdx": "Apache-2.0",
        "page_kind": "product",
    }


def test_already_exists_is_not_rewritten_by_an_unknown_web_row(tmp_path):
    judged = _judge(tmp_path, _web(), prior=[_mature(), _web()])
    assert judged[0] == "ALREADY_EXISTS"
    assert judged.unknown_blocker is None
    assert "UNKNOWN_BLOCKER_FACTS" not in judged[1]
    assert "erudika/para" in judged[1]


def test_selfdb_style_unmatched_name_stays_already_exists(tmp_path):
    """A verified mature blocker still refuses the repo when the model names something else."""
    judged = _judge(
        tmp_path,
        _web(),
        verdict="ALREADY_EXISTS",
        alternative_repo="Supabase",
        alternative_quote="open source backend as a service",
        prior=[_mature(), _web()],
    )
    assert judged[0] == "ALREADY_EXISTS"
    assert judged[0] != "NEEDS_REVIEW"
    assert judged[0] != "NEEDS_PRIOR_ART"
    assert judged.unknown_blocker is None
    assert "erudika/para" in judged[1]


def test_discovery_filter_payload_records_the_rule():
    from scripts.score_candidates import _discovery_output

    record = {
        "fired": True,
        "rule": "UNKNOWN_BLOCKER_FACTS",
        "alternative": "web:https://example.com/folder-sync",
        "fields": ["push/release date", "stars"],
    }
    payload = _discovery_output(
        "NEEDS_REVIEW",
        "UNKNOWN_BLOCKER_FACTS: web:https://example.com/folder-sync full overlap, push/release date, stars not established",
        "csv",
        unknown_blocker=record,
    )
    assert payload["unknown_blocker"]["fired"] is True
    assert payload["unknown_blocker"]["alternative"] == "web:https://example.com/folder-sync"
    assert payload["unknown_blocker"]["fields"] == ["push/release date", "stars"]
    assert "unknown_blocker" not in _discovery_output("CANDIDATE", "ok", "csv")
