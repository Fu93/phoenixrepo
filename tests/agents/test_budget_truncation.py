"""A prior-art search cut short by the tool budget must never become CANDIDATE."""

from __future__ import annotations

import json
from pathlib import Path

from app.agents.relevance import RelevanceClient, judge_discovery, search_prior_art
from app.tools.base import Budget, ToolBox, ToolResult
from scripts.rerun_unjudged import is_infra_review


class _Tool:
    def __init__(self, name: str, items: list[dict]) -> None:
        self.name = name
        self.items = items

    def call(self, args):
        return ToolResult(ok=True, data={"items": self.items}, error="", source_url="u", fetched_at="t")


def _box(max_calls: int) -> ToolBox:
    return ToolBox(
        tools={
            "github": _Tool("github", [{"full_name": "other/alt", "readme": "r"}]),
            "npm": _Tool("npm", [{"name": "alt-pkg", "description": "d"}]),
            "web_search": _Tool("web_search", [{"url": "https://example.com/a", "title": "t", "snippet": "s"}]),
        },
        budget=Budget(max_calls=max_calls, max_seconds=600),
    )


def test_budget_cut_marks_the_unfinished_sources():
    found = search_prior_art({"repo_id": "o/r"}, ["q1", "q2", "q3"], _box(max_calls=3))
    assert found.searched_queries == 1 and found.planned_queries == 3
    assert found.source_status == {"github": "BUDGET_EXHAUSTED", "npm": "BUDGET_EXHAUSTED", "web": "BUDGET_EXHAUSTED"}
    assert found.insufficient_coverage is True
    assert len(found) > 0  # rows were found before the cut; that is still not a full search


def test_a_finished_search_is_not_marked():
    found = search_prior_art({"repo_id": "o/r"}, ["q1", "q2"], _box(max_calls=50))
    assert set(found.source_status.values()) == {"ok"}
    assert found.searched_queries == 2


def test_budget_cut_blocks_candidate_even_with_alternatives_found(tmp_path: Path):
    candidate = {"repo_id": "test/tool", "description": "A developer tool.", "readme_excerpt": "A developer tool solving friction."}
    client = RelevanceClient(
        cache_dir=tmp_path / "cache",
        mock_handler=lambda p, s: json.dumps({"verdict": "CANDIDATE", "reason": "novel", "problem_quote": "A developer tool solving friction"}),
    )
    verdict, reason = judge_discovery(
        candidate=candidate,
        prior_rows=[],
        demand_rows=[],
        client=client,
        source_counts={"github": 2, "npm": 1, "web": 1},
        source_status={"github": "ok", "npm": "BUDGET_EXHAUSTED", "web": "BUDGET_EXHAUSTED"},
        insufficient_coverage=True,
    )
    assert verdict == "NEEDS_REVIEW"
    assert "tool budget exhausted" in reason and "npm, web" in reason
    # The rerun and the eval treat it as unfinished work, not as a judgement.
    assert is_infra_review({"verdict": verdict, "reason": reason})


def test_must_beat_and_own_identity_paths_cannot_pass_a_cut_search(tmp_path):
    import json

    from app.agents.relevance import RelevanceClient, judge_discovery

    cand = {"repo_id": "me/tool", "description": "Sync tool.", "readme_excerpt": "A small sync tool for notes."}
    quote = "sync notes between two machines"
    immature = {
        "alternative_id": "other/tiny", "url": "https://github.com/other/tiny", "same_problem": True, "overlap": "full",
        "quotes": [quote], "description": quote, "stars": 3, "pushed_at": "2026-10-01T00:00:00Z", "archived": False,
        "licence_spdx": "MIT", "page_kind": "product",
    }

    def says(alt: str):
        return lambda prompt, sys: json.dumps({
            "verdict": "ALREADY_EXISTS", "reason": "r", "problem_quote": "A small sync tool for notes",
            "alternative_repo": alt, "alternative_quote": quote,
        })

    status = {"github": "BUDGET_EXHAUSTED", "npm": "ok", "web": "BUDGET_EXHAUSTED"}
    for alt, rows in (("other/tiny", [immature]), ("me/tool", [])):
        client = RelevanceClient(cache_dir=tmp_path / alt.replace("/", "_"), mock_handler=says(alt))
        verdict, reason = judge_discovery(cand, prior_rows=[], demand_rows=[], client=client, problem_alternatives=rows,
                                          source_status=status, source_counts={"github": 1, "npm": 0, "web": 0})
        assert verdict == "NEEDS_REVIEW", (alt, reason)
        assert "tool budget exhausted" in reason
