"""Rescue-potential score. It ranks rows and never changes a verdict."""
from datetime import date

from app.agents.rescue import count_external_breakage, failure_title, rescue_potential
from app.first_gate import apply_first_gate
from scripts.evidence_report import render_repo_html
from scripts.score_candidates import _render_markdown


def test_failure_titles_and_external_breakage():
    assert failure_title("i can't use it successful")
    assert failure_title("Value change - No Hit")
    assert failure_title("breakpoints hit no data")
    assert failure_title("tool has no effect")
    issues = [
        {"title": "i can't use it successful", "comments": 0, "user": {"login": "a"}},
        {"title": "Value change - No Hit", "comments": 1, "user": {"login": "b"}},
        {"title": "breakpoints hit no data", "comments": 0, "user": {"login": "c"}},
        {"title": "tool has no effect", "comments": 1, "user": {"login": "d"}},
        {"title": "thanks", "comments": 0, "user": {"login": "e"}},
        {"title": "error from the owner", "comments": 0, "user": {"login": "ri-char"}},
        {"title": "error with a long thread", "comments": 4, "user": {"login": "f"}},
        {"title": "error in a pull request", "comments": 0, "user": {"login": "g"}, "pull_request": {"url": "https://github.com/ri-char/pwatch/pull/1"}},
    ]
    assert count_external_breakage(issues, "ri-char") == 4


def test_pwatch_shaped_facts_keep_the_components_and_do_not_change_a_refusal():
    facts = {
        "stars": 265,
        "forks": 64,
        "open_issues": 4,
        "pushed_at": "2024-07-06T00:00:00Z",
        "external_breakage": 4,
        "ci_state": "ci_configured_zero_runs",
        "ci_date": None,
        "dependabot_open": 1,
        "renovate_open": 0,
        "source": "github_api",
    }
    score = rescue_potential(facts, as_of=date(2026, 10, 25))
    comp = score["components"]
    assert score["changes_verdict"] is False
    assert "verdict" not in score
    assert comp["stars"] == 265
    assert comp["forks"] == 64
    assert comp["staleness_days"] == 841
    assert comp["stars_vs_staleness"] is not None
    assert comp["ci_state"] == "ci_configured_zero_runs"
    assert comp["external_breakage"] == 4
    assert comp["dependabot_open"] == 1
    assert comp["open_issues_count"] == 4
    row = {"repo_id": "ri-char/pwatch", "verdict": "ALREADY_EXISTS", "rescue_potential": score}
    assert row["verdict"] == "ALREADY_EXISTS"
    decision, _reason = apply_first_gate(
        "RESURRECTED",
        "tests passed",
        {"verdict": row["verdict"], "reason": "refused", "rescue_potential": score},
    )
    assert decision != "RESURRECTED"


def test_markdown_and_html_show_the_components_without_flipping_the_verdict():
    score = rescue_potential(
        {
            "stars": 265,
            "forks": 64,
            "pushed_at": "2024-07-06",
            "external_breakage": 4,
            "ci_state": "ci_configured_zero_runs",
            "dependabot_open": 2,
            "renovate_open": 1,
        },
        as_of=date(2026, 10, 25),
    )
    md = _render_markdown({
        "date": "2026-10-26",
        "run_hash": "abc",
        "command": "test",
        "llm_used": False,
        "model_calls": {},
        "warnings": [],
        "protagonist": {"reason": "none"},
        "candidates": [],
        "ineligible": [{
            "repo_id": "ri-char/pwatch",
            "verdict": "ALREADY_EXISTS",
            "reason": "refused on a syscall",
            "rescue_potential": score,
            "must_beat": [{
                "alternative": "perf_event_open",
                "overlap": "none",
                "quote": "a kernel syscall",
                "reason": "did not block: syscall is not an alternative",
            }],
        }],
    })
    assert "Rescue potential" in md
    assert "This score does not change the verdict. A refused repository stays refused." in md
    assert "ci_configured_zero_runs" in md
    assert "841" in md
    assert "perf_event_open" in md
    assert "ALREADY_EXISTS" in md
    html = render_repo_html(
        "ri-char/pwatch",
        "ALREADY_EXISTS",
        "refused on a syscall",
        {
            "rescue": score,
            "must_beat": [{
                "alternative": "perf_event_open",
                "overlap": "none",
                "quote": "a kernel syscall",
                "reason": "did not block: syscall is not an alternative",
            }],
        },
    )
    assert "Rescue potential" in html
    assert "This score does not change the verdict." in html
    assert "ci_configured_zero_runs" in html
    assert "Alternatives that did not block" in html
    assert "ALREADY_EXISTS" in html
