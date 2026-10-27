"""Post-v3 quote-failure behaviour. The scored v3 traces are not inputs here.

PHOENIX_QUOTE_FAIL_FLAG defaults off. When it is on, a failed verbatim quote on
a full-overlap claim is retried once. If that retry still does not verify, the
claim is not a competitor and it cannot return CANDIDATE or ALREADY_EXISTS.
There is no minimum quote length.
"""
from __future__ import annotations

import json

from app.agents.relevance import (
    FallbackBudget,
    RelevanceClient,
    filter_relevant_alternatives,
    judge_discovery,
    quote_fail_flag_enabled,
)

PROBLEM_QUOTE = "Generate invoices from a browser form"
CANDIDATE = {
    "repo_id": "acme/invoicer",
    "description": "Generate invoices from a browser form for freelancers.",
    "readme_excerpt": "Generate invoices from a browser form and export a PDF.",
    "stars": 3,
}
PROBLEM = {
    "problem": PROBLEM_QUOTE,
    "target_user": "freelancers",
    "category": "invoicing",
}
GOOD_QUOTE = "creates PDF invoices for freelancers online"
RIVAL_TEXT = "This tool creates PDF invoices for freelancers online with templates."
BAD_QUOTE = "this quote is definitely not in the alternative text at all"
STRICT_MARK = "STRICT VERBATIM QUOTE"


def _rival(name: str, text: str = RIVAL_TEXT) -> dict:
    return {
        "alternative_id": name,
        "url": f"https://github.com/{name}",
        "description": text,
        "readme": "",
        "source": "github",
        "stars": 800,
        "pushed_at": "2026-10-01T00:00:00Z",
        "licence_spdx": "MIT",
        "archived": False,
    }


def _alt_json(name_in_prompt: str, *, overlap: str, quotes: list[str], same: bool = True) -> str:
    return json.dumps({
        "same_problem": same,
        "overlap": overlap if same else "none",
        "reason": "judgement",
        "quotes": quotes,
    })


def _batch_json(entries: list[dict]) -> str:
    return json.dumps({"results": entries})


def _discovery(verdict: str = "CANDIDATE") -> str:
    return json.dumps({
        "verdict": verdict,
        "reason": "the model called it new",
        "problem_quote": PROBLEM_QUOTE,
        "alternative_repo": None,
        "alternative_quote": None,
    })


def _client(tmp_path, handler):
    return RelevanceClient(cache_dir=tmp_path / "cache", mock_handler=handler)


def _judge(tmp_path, rows, claims):
    client = _client(tmp_path / "disc", lambda prompt, sys: _discovery())
    return judge_discovery(
        CANDIDATE,
        prior_rows=[],
        demand_rows=[],
        client=client,
        problem_alternatives=list(rows),
        unverified_full_overlap_claims=claims,
    )


def test_quote_fail_flag_defaults_off_and_recognises_on_values(monkeypatch):
    monkeypatch.delenv("PHOENIX_QUOTE_FAIL_FLAG", raising=False)
    assert quote_fail_flag_enabled() is False
    monkeypatch.setenv("PHOENIX_QUOTE_FAIL_FLAG", "  ")
    assert quote_fail_flag_enabled() is False
    for off in ("0", "off", "false", "no", "disabled", "OFF", " False ", "maybe"):
        monkeypatch.setenv("PHOENIX_QUOTE_FAIL_FLAG", off)
        assert quote_fail_flag_enabled() is False
    for on in ("1", "on", "true", "yes", "enabled", "ON", " True "):
        monkeypatch.setenv("PHOENIX_QUOTE_FAIL_FLAG", on)
        assert quote_fail_flag_enabled() is True


def test_failing_full_overlap_quote_retries_then_needs_review_never_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_QUOTE_FAIL_FLAG", "1")
    calls: list[str] = []

    def llm(prompt: str, sys: str) -> str:
        calls.append(prompt)
        if "Judge whether this candidate is CANDIDATE" in prompt:
            return _discovery()
        if STRICT_MARK in prompt:
            return _alt_json("rival/biller", overlap="full", quotes=[BAD_QUOTE])
        return _alt_json("rival/biller", overlap="full", quotes=[BAD_QUOTE])

    client = _client(tmp_path, llm)
    budget = FallbackBudget(seconds=1000, clock=lambda: 0.0)
    rows = filter_relevant_alternatives(
        PROBLEM, CANDIDATE["repo_id"], [_rival("rival/biller")], client=client, fallback_budget=budget,
    )
    claims = list(getattr(rows, "unverified_full_overlap_claims", []) or [])
    assert rows == []
    assert [c["alternative_id"] for c in claims] == ["rival/biller"]
    assert all(c.get("quote_verified") is False for c in claims)
    assert budget.completed_calls == 2
    assert sum(1 for prompt in calls if STRICT_MARK in prompt) == 1
    judged = judge_discovery(
        CANDIDATE, prior_rows=[], demand_rows=[], client=client,
        problem_alternatives=list(rows), unverified_full_overlap_claims=claims,
    )
    verdict, reason = judged
    assert verdict == "NEEDS_REVIEW"
    assert verdict != "CANDIDATE"
    assert verdict != "ALREADY_EXISTS"
    assert "unverified full-overlap claim" in reason
    assert "rival/biller" in reason
    assert judged.evidence == []


def test_batch_full_overlap_failures_list_every_name_and_never_block(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_QUOTE_FAIL_FLAG", "1")
    def llm(prompt: str, sys: str) -> str:
        if "BATCH MODE" in sys:
            return _batch_json([
                {"index": 0, "same_problem": True, "overlap": "full", "reason": "same", "quotes": [BAD_QUOTE]},
                {"index": 1, "same_problem": True, "overlap": "full", "reason": "same", "quotes": [BAD_QUOTE]},
            ])
        if STRICT_MARK in prompt:
            return _alt_json("x", overlap="full", quotes=[BAD_QUOTE])
        raise AssertionError(prompt[:180])

    client = _client(tmp_path, llm)
    rows = filter_relevant_alternatives(
        PROBLEM, CANDIDATE["repo_id"],
        [_rival("rival/one"), _rival("rival/two")],
        client=client,
    )
    claims = list(getattr(rows, "unverified_full_overlap_claims", []) or [])
    assert [r.get("alternative_id") for r in rows] == []
    assert [c["alternative_id"] for c in claims] == ["rival/one", "rival/two"]
    judged = _judge(tmp_path, rows, claims)
    verdict, reason = judged
    assert verdict == "NEEDS_REVIEW"
    assert "unverified full-overlap claim" in reason
    assert "rival/one" in reason and "rival/two" in reason
    assert judged.evidence == []
    assert verdict != "ALREADY_EXISTS"


def test_retry_success_is_a_normal_verified_competitor(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_QUOTE_FAIL_FLAG", "1")
    def llm(prompt: str, sys: str) -> str:
        if "Judge whether this candidate is CANDIDATE" in prompt:
            return _discovery()
        if STRICT_MARK in prompt:
            return _alt_json("rival/biller", overlap="full", quotes=[GOOD_QUOTE])
        return _alt_json("rival/biller", overlap="full", quotes=[BAD_QUOTE])

    client = _client(tmp_path, llm)
    rows = filter_relevant_alternatives(
        PROBLEM, CANDIDATE["repo_id"], [_rival("rival/biller")], client=client,
    )
    assert [r["alternative_id"] for r in rows] == ["rival/biller"]
    assert rows[0]["quote_verified"] is True
    assert rows[0]["overlap"] == "full"
    assert GOOD_QUOTE in rows[0]["quotes"]
    assert getattr(rows, "unverified_full_overlap_claims", []) == []
    judged = judge_discovery(
        CANDIDATE, prior_rows=[], demand_rows=[], client=client, problem_alternatives=list(rows),
    )
    verdict, reason = judged
    assert verdict == "ALREADY_EXISTS"
    assert "rival/biller" in reason
    assert judged.evidence and judged.evidence[0]["alternative"] == "rival/biller"


def test_flag_off_keeps_the_old_drop_without_retry_or_downgrade(tmp_path, monkeypatch):
    monkeypatch.delenv("PHOENIX_QUOTE_FAIL_FLAG", raising=False)
    calls: list[str] = []

    def llm(prompt: str, sys: str) -> str:
        calls.append(prompt)
        return _alt_json("rival/biller", overlap="full", quotes=[BAD_QUOTE])

    client = _client(tmp_path, llm)
    budget = FallbackBudget(seconds=1000, clock=lambda: 0.0)
    rows = filter_relevant_alternatives(
        PROBLEM, CANDIDATE["repo_id"], [_rival("rival/biller")], client=client, fallback_budget=budget,
    )
    assert rows == []
    assert getattr(rows, "unverified_full_overlap_claims", []) == []
    assert budget.completed_calls == 1
    assert not any(STRICT_MARK in prompt for prompt in calls)
    judged = _judge(tmp_path, rows, [])
    verdict, reason = judged
    assert verdict == "CANDIDATE"
    assert "unverified full-overlap claim" not in reason


def test_partial_overlap_failed_quote_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_QUOTE_FAIL_FLAG", "1")
    calls: list[str] = []

    def llm(prompt: str, sys: str) -> str:
        calls.append(prompt)
        return _alt_json("rival/biller", overlap="partial", quotes=[BAD_QUOTE])

    client = _client(tmp_path, llm)
    rows = filter_relevant_alternatives(
        PROBLEM, CANDIDATE["repo_id"], [_rival("rival/biller")], client=client,
    )
    assert rows == []
    assert getattr(rows, "unverified_full_overlap_claims", []) == []
    assert len(calls) == 1
    assert STRICT_MARK not in calls[0]


def test_unverified_row_cannot_block_even_when_its_quote_is_a_substring(tmp_path, monkeypatch):
    """A full-overlap claim that failed verification is not a competitor."""
    monkeypatch.setenv("PHOENIX_QUOTE_FAIL_FLAG", "1")
    leaked = {
        "alternative_id": "rival/biller",
        "url": "https://github.com/rival/biller",
        "same_problem": True,
        "overlap": "full",
        "quotes": [GOOD_QUOTE],
        "excerpt": RIVAL_TEXT,
        "stars": 800,
        "pushed_at": "2026-10-01T00:00:00Z",
        "licence_spdx": "MIT",
        "archived": False,
        "page_kind": "product",
        "quote_verified": False,
    }
    def llm(prompt: str, sys: str) -> str:
        return json.dumps({
            "verdict": "ALREADY_EXISTS",
            "reason": "rival already does this",
            "problem_quote": PROBLEM_QUOTE,
            "alternative_repo": "rival/biller",
            "alternative_quote": GOOD_QUOTE,
        })

    client = _client(tmp_path, llm)
    judged = judge_discovery(
        CANDIDATE,
        prior_rows=[],
        demand_rows=[],
        client=client,
        problem_alternatives=[leaked],
        unverified_full_overlap_claims=[{
            "alternative_id": "rival/biller",
            "overlap": "full",
            "quote_verified": False,
        }],
    )
    verdict, reason = judged
    assert verdict != "ALREADY_EXISTS"
    assert verdict == "NEEDS_REVIEW"
    assert "unverified full-overlap claim" in reason
    assert all(item.get("alternative") != "rival/biller" or item.get("quote") != GOOD_QUOTE for item in judged.evidence)
    assert not any(item.get("quote") == GOOD_QUOTE for item in judged.evidence)
