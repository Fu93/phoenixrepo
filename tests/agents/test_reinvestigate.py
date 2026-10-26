"""Tests for the automatic re-investigation loop using capped sub-agents."""

from __future__ import annotations

import httpx
import pytest

from app.agents.reinvestigate import ReinvestigationResult, reinvestigate
from app.agents.subagents import SubAgentManager
from app.agents.venture import (
    Criterion,
    Scorecard,
    review_scorecard,
)
from app.tools import Budget, GitHubTool, ToolBox, WebFetchTool


def test_reinvestigate_improves_criterion_when_fake_tool_returns_evidence():
    # Candidate initially has unevidenced demand score=2, which is clamped to 0
    card = Scorecard(
        repo_id="SergioMendolia/ytkb",
        verdict="CANDIDATE",
        problem_severity=Criterion(score=2, evidence_ids=["ev-prob"], rationale="Pain"),
        demand_evidence=Criterion(score=2, evidence_ids=[], rationale="High external demand"),  # Missing evidence!
        differentiation=Criterion(score=2, evidence_ids=["ev-diff"], rationale="Diff"),
        feasibility=Criterion(score=2, evidence_ids=["ev-feas"], rationale="Feas"),
        productization_path=Criterion(score=1, evidence_ids=["ev-prod"], rationale="Prod"),
        moat=Criterion(score=0, evidence_ids=[], rationale="No moat"),
    )
    initial_evidence = [
        {"id": "ev-prob", "url": "https://example.com/p", "text": "Struggling job", "content_hash": "h1"},
        {"id": "ev-diff", "url": "https://example.com/d", "text": "Differentiation", "content_hash": "h2"},
        {"id": "ev-feas", "url": "https://example.com/f", "text": "Feasibility tests", "content_hash": "h3"},
        {"id": "ev-prod", "url": "https://example.com/pr", "text": "Product path", "content_hash": "h4"},
    ]

    # Mock tool returns an external demand issue on an alternative
    def mock_transport_fn(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"items": [{"title": "Demand on Foliant PR 80 for YouTrack KB sync", "comments": 3}]},
        )

    transport = httpx.MockTransport(mock_transport_fn)
    tools = {"github": GitHubTool(transport=transport)}
    toolbox = ToolBox(tools=tools, budget=Budget(max_calls=10))

    # Fake analyst generates a grounded score using the newly gathered evidence ID
    def fake_analyst(criterion, repo_id, evidence, scorecard):
        if criterion == "demand_evidence":
            # Find the new tool evidence item
            tool_items = [e for e in evidence if str(e.get("id")).startswith("tool:")]
            if tool_items:
                return Criterion(
                    score=2,
                    evidence_ids=[tool_items[0]["id"]],
                    rationale="Verified external demand from tool evidence.",
                )
        return Criterion(score=0, evidence_ids=[], rationale="No evidence")

    result = reinvestigate(
        scorecard=card,
        evidence=initial_evidence,
        toolbox=toolbox,
        analyst=fake_analyst,
        max_rounds=2,
    )

    assert result.status == "ok"
    assert result.rounds_executed >= 1
    # Demand was re-scored and successfully clamped as SUPPORTED
    assert result.scorecard.demand_evidence.score == 2
    assert result.scorecard.demand_evidence.status == "SUPPORTED"
    assert result.scorecard.demand_evidence.evidence_ids[0].startswith("tool:")
    # Scorecard total score increased
    assert result.scorecard.total > 0
    # Unpacking works as (scorecard, evidence)
    sc, ev = result
    assert sc.repo_id == "SergioMendolia/ytkb"
    assert any(e["id"].startswith("tool:") for e in ev)


def test_reinvestigate_falls_back_to_clamped_when_budget_is_zero():
    card = Scorecard(
        repo_id="test/repo",
        verdict="CANDIDATE",
        problem_severity=Criterion(score=2, evidence_ids=["fake-id"], rationale="Pain"),  # fake-id missing
        demand_evidence=Criterion(score=2, evidence_ids=["fake-id"], rationale="Demand"),
        differentiation=Criterion(score=2, evidence_ids=["fake-id"], rationale="Diff"),
        feasibility=Criterion(score=2, evidence_ids=["fake-id"], rationale="Feas"),
        productization_path=Criterion(score=1, evidence_ids=["fake-id"], rationale="Prod"),
        moat=Criterion(score=0, evidence_ids=[], rationale="Moat"),
    )
    # Budget with 0 calls
    toolbox = ToolBox(budget=Budget(max_calls=0))

    result = reinvestigate(
        scorecard=card,
        evidence=[],
        toolbox=toolbox,
        max_rounds=2,
    )

    assert result.status == "BUDGET_EXHAUSTED"
    # Deterministic clamp: all scores with fake-id clamped to 0
    assert result.scorecard.problem_severity.score == 0
    assert result.scorecard.problem_severity.status == "UNSUPPORTED"
    assert result.scorecard.demand_evidence.score == 0
    assert result.scorecard.total == 0.0


def test_reinvestigate_records_llm_fallback_when_rounds_exhausted():
    card = Scorecard(
        repo_id="test/repo",
        verdict="CANDIDATE",
        problem_severity=Criterion(score=2, evidence_ids=["ev1"], rationale="Pain"),
        demand_evidence=Criterion(score=2, evidence_ids=["fake-id"], rationale="Demand"),
        differentiation=Criterion(score=2, evidence_ids=["ev1"], rationale="Diff"),
        feasibility=Criterion(score=2, evidence_ids=["ev1"], rationale="Feas"),
        productization_path=Criterion(score=1, evidence_ids=["ev1"], rationale="Prod"),
        moat=Criterion(score=0, evidence_ids=[], rationale="Moat"),
    )
    # Empty tools: sub-agent finds no evidence
    toolbox = ToolBox(tools={}, budget=Budget(max_calls=10))

    # Analyst that continues to claim unsupported evidence
    def obstinate_analyst(criterion, repo_id, evidence, scorecard):
        return Criterion(score=2, evidence_ids=["still-missing-id"], rationale="Stubbornly unsupported")

    result = reinvestigate(
        scorecard=card,
        evidence=[{"id": "ev1"}],
        toolbox=toolbox,
        analyst=obstinate_analyst,
        max_rounds=1,
    )

    # When rounds are exhausted with unresolved rework issues, status is LLM_FALLBACK
    assert result.status == "LLM_FALLBACK"
    # Deterministic guard clamps the score to 0 regardless of analyst claims
    assert result.scorecard.demand_evidence.score == 0
    assert result.scorecard.demand_evidence.status == "UNSUPPORTED"


def test_demand_budget_exhausted_is_a_flag_and_the_verdict_stays():
    card = Scorecard(
        repo_id="goodreasonai/ScrapeServ",
        verdict="CANDIDATE",
        metadata={"alternative_repos": [f"org/alt-{i}" for i in range(6)]},
        problem_severity=Criterion(score=2, evidence_ids=["ev1"], rationale="Pain"),
        demand_evidence=Criterion(score=1, evidence_ids=[], rationale="Demand was not cited"),
        differentiation=Criterion(score=2, evidence_ids=["ev1"], rationale="Diff"),
        feasibility=Criterion(score=2, evidence_ids=["ev1"], rationale="Feas"),
        productization_path=Criterion(score=1, evidence_ids=["ev1"], rationale="Prod"),
        moat=Criterion(score=0, evidence_ids=[], rationale="Moat"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [{"title": "Someone asked for this", "comments": 2}]})

    toolbox = ToolBox(
        tools={"github": GitHubTool(transport=httpx.MockTransport(handler))},
        budget=Budget(max_calls=40),
    )
    result = reinvestigate(scorecard=card, evidence=[{"id": "ev1"}], toolbox=toolbox, max_rounds=1)
    assert result.scorecard.verdict == "CANDIDATE"
    assert "BUDGET_EXHAUSTED" in result.flags
    assert result.status != "BUDGET_EXHAUSTED"


def test_reinvestigate_deterministic_guard_always_has_last_word():
    card = Scorecard(
        repo_id="test/repo",
        verdict="CANDIDATE",
        problem_severity=2,
        demand_evidence=2,
        differentiation=2,
        feasibility=2,
        productization_path=1,
        moat=0,
    )
    toolbox = ToolBox(budget=Budget(max_calls=10))

    # Analyst tries to award score 3 with a fabricated evidence ID
    def cheating_analyst(criterion, repo_id, evidence, scorecard):
        return Criterion(score=3, evidence_ids=["hallucinated:123"], rationale="Claiming maximum points")

    result = reinvestigate(
        scorecard=card,
        evidence=[],  # No evidence in set
        toolbox=toolbox,
        analyst=cheating_analyst,
        max_rounds=1,
    )

    # Clamping clamps fabricated score to 0
    assert result.scorecard.demand_evidence.score == 0
    assert result.scorecard.demand_evidence.status == "UNSUPPORTED"
    assert result.scorecard.problem_severity.score == 0
    assert result.scorecard.problem_severity.status == "UNSUPPORTED"
