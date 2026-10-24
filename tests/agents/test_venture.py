"""Tests for the venture scorecard, clamping guard, and protagonist selection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.venture import (
    CRITERIA_NAMES,
    CRITERIA_WEIGHTS,
    Criterion,
    ProtagonistResult,
    ReworkRequest,
    Scorecard,
    calculate_weighted_total,
    clamp_criterion,
    is_eligible_candidate,
    review_scorecard,
    select_protagonist,
)

ROOT = Path(__file__).resolve().parents[2]
CASES = json.loads((ROOT / "data" / "eval" / "cases.json").read_text(encoding="utf-8"))["cases"]


def _case(repo_id: str) -> dict:
    return next(case for case in CASES if case["repo_id"] == repo_id)


# ---------------------------------------------------------------------------
# 1. Clamping Tests
# ---------------------------------------------------------------------------


def test_clamping_unsupported_positive_scores():
    evidence_set = {"demand:foliant-pr-80", "candidate:SergioMendolia/ytkb"}

    # Positive score with no evidence_ids -> clamped to 0 and flagged UNSUPPORTED
    c1 = Criterion(score=2, evidence_ids=[], rationale="User struggling with docs sync.")
    c1.clamp(evidence_set)
    assert c1.score == 0
    assert c1.original_score == 2
    assert c1.status == "UNSUPPORTED"
    assert c1.flagged == "UNSUPPORTED"
    assert c1.unsupported is True

    # Positive score with unknown evidence id -> clamped to 0 and flagged UNSUPPORTED
    c2 = Criterion(score=3, evidence_ids=["demand:hallucinated-id"], rationale="Huge user base.")
    clamp_criterion(c2, evidence_set)
    assert c2.score == 0
    assert c2.original_score == 3
    assert c2.status == "UNSUPPORTED"
    assert c2.unsupported is True

    # Positive score with valid evidence id -> preserved
    c3 = Criterion(score=2, evidence_ids=["demand:foliant-pr-80"], rationale="External PR shows demand.")
    clamp_criterion(c3, evidence_set)
    assert c3.score == 2
    assert c3.original_score is None
    assert c3.status == "SUPPORTED"
    assert c3.unsupported is False

    # Score of 0 with no evidence -> remains 0, supported (e.g. no moat is normal)
    c4 = Criterion(score=0, evidence_ids=[], rationale="No moat.")
    clamp_criterion(c4, evidence_set)
    assert c4.score == 0
    assert c4.status == "SUPPORTED"
    assert c4.unsupported is False


def test_scorecard_clamp_in_place():
    evidence_set = {"candidate:repo1"}
    card = Scorecard(
        repo_id="owner/repo1",
        verdict="CANDIDATE",
        problem_severity=Criterion(score=2, evidence_ids=["candidate:repo1"], rationale="Pain"),
        demand_evidence=Criterion(score=2, evidence_ids=["fake:demand"], rationale="Unverified pull"),
        differentiation=Criterion(score=2, evidence_ids=["candidate:repo1"], rationale="Diff"),
        feasibility=Criterion(score=2, evidence_ids=["candidate:repo1"], rationale="Feas"),
        productization_path=Criterion(score=1, evidence_ids=["candidate:repo1"], rationale="Prod"),
        moat=Criterion(score=0, evidence_ids=[], rationale="No moat"),
    )
    # Before clamping, demand contributes points
    assert card.demand_evidence.score == 2
    card.clamp(evidence_set)
    # After clamping, demand is clamped to 0 because fake:demand is not in evidence_set
    assert card.demand_evidence.score == 0
    assert card.demand_evidence.status == "UNSUPPORTED"
    assert card.problem_severity.score == 2


# ---------------------------------------------------------------------------
# 2. Ineligibility Tests
# ---------------------------------------------------------------------------


def test_ineligibility_of_already_exists_and_no_go():
    card_ae = Scorecard(repo_id="org/wheel", verdict="ALREADY_EXISTS")
    card_nogo = Scorecard(repo_id="org/bad-idea", verdict="NO_GO")
    card_cand = Scorecard(repo_id="org/good-idea", verdict="CANDIDATE")

    assert card_ae.is_eligible is False
    assert is_eligible_candidate(card_ae) is False
    assert is_eligible_candidate("ALREADY_EXISTS") is False

    assert card_nogo.is_eligible is False
    assert is_eligible_candidate(card_nogo) is False
    assert is_eligible_candidate("NO_GO") is False

    assert card_cand.is_eligible is True
    assert is_eligible_candidate(card_cand) is True
    assert is_eligible_candidate("CANDIDATE") is True


def test_ineligible_repos_are_excluded_from_protagonist_selection():
    # Even if ALREADY_EXISTS has a perfect score 100 and feasibility 3, it cannot be selected
    wheel = Scorecard(
        repo_id="org/perfect-wheel",
        verdict="ALREADY_EXISTS",
        problem_severity=3,
        demand_evidence=3,
        differentiation=3,
        feasibility=3,
        productization_path=3,
        moat=3,
    )
    candidate = Scorecard(
        repo_id="org/modest-candidate",
        verdict="CANDIDATE",
        problem_severity=2,
        demand_evidence=2,
        differentiation=2,
        feasibility=2,
        productization_path=1,
        moat=0,
    )
    winner, reason = select_protagonist([wheel, candidate])
    assert winner is not None
    assert winner.repo_id == "org/modest-candidate"
    assert "Selected protagonist org/modest-candidate" in reason


# ---------------------------------------------------------------------------
# 3. Weights and Total Calculation Tests
# ---------------------------------------------------------------------------


def test_weights_sum_to_100():
    assert sum(CRITERIA_WEIGHTS.values()) == 100
    assert CRITERIA_WEIGHTS == {
        "problem_severity": 20,
        "demand_evidence": 20,
        "differentiation": 20,
        "feasibility": 15,
        "productization_path": 15,
        "moat": 10,
    }


def test_weighted_total_all_maximum():
    card = Scorecard(
        repo_id="org/top",
        verdict="CANDIDATE",
        problem_severity=3,
        demand_evidence=3,
        differentiation=3,
        feasibility=3,
        productization_path=3,
        moat=3,
    )
    assert card.total == 100.0
    assert card.weighted_total == 100.0


def test_weighted_total_all_zero():
    card = Scorecard(
        repo_id="org/zero",
        verdict="CANDIDATE",
        problem_severity=0,
        demand_evidence=0,
        differentiation=0,
        feasibility=0,
        productization_path=0,
        moat=0,
    )
    assert card.total == 0.0


def test_weighted_total_individual_weights():
    # Only problem_severity = 3 (weight 20)
    card_prob = Scorecard("org/p", "CANDIDATE", problem_severity=3)
    assert card_prob.total == 20.0

    # Only feasibility = 3 (weight 15)
    card_feas = Scorecard("org/f", "CANDIDATE", feasibility=3)
    assert card_feas.total == 15.0

    # Only moat = 3 (weight 10)
    card_moat = Scorecard("org/m", "CANDIDATE", moat=3)
    assert card_moat.total == 10.0


def test_weighted_total_partial_scores():
    # Each criterion scores 2 (two-thirds of maximum): 100 * (2/3) = 66.67
    card = Scorecard(
        repo_id="org/partial",
        verdict="CANDIDATE",
        problem_severity=2,
        demand_evidence=2,
        differentiation=2,
        feasibility=2,
        productization_path=2,
        moat=2,
    )
    assert card.total == 66.67

    # Custom mix:
    # problem: 20 * (3/3) = 20.0
    # demand: 20 * (2/3) = 13.33
    # differentiation: 20 * (2/3) = 13.33
    # feasibility: 15 * (2/3) = 10.0
    # productization: 15 * (2/3) = 10.0
    # moat: 10 * (1/3) = 3.33
    # sum = 20 + 13.333... + 13.333... + 10 + 10 + 3.333... = 70.0
    card2 = Scorecard(
        repo_id="org/mix",
        verdict="CANDIDATE",
        problem_severity=3,
        demand_evidence=2,
        differentiation=2,
        feasibility=2,
        productization_path=2,
        moat=1,
    )
    assert card2.total == 70.0


def test_calculate_weighted_total_standalone():
    raw_dict = {
        "problem_severity": 3,
        "demand_evidence": 3,
        "differentiation": 3,
        "feasibility": 3,
        "productization_path": 3,
        "moat": 3,
    }
    assert calculate_weighted_total(raw_dict) == 100.0


# ---------------------------------------------------------------------------
# 4. Protagonist Selection and Feasibility Gate Tests
# ---------------------------------------------------------------------------


def test_feasibility_gate_disqualifies_low_feasibility():
    # High score (e.g. 85) but feasibility = 1 (< 2)
    high_debt = Scorecard(
        repo_id="org/high-debt",
        verdict="CANDIDATE",
        problem_severity=3,
        demand_evidence=3,
        differentiation=3,
        feasibility=1,  # Disqualified by feasibility < 2
        productization_path=3,
        moat=2,
    )
    # Lower total score but feasibility = 2 (qualifies)
    clean_baseline = Scorecard(
        repo_id="org/clean-baseline",
        verdict="CANDIDATE",
        problem_severity=2,
        demand_evidence=2,
        differentiation=2,
        feasibility=2,
        productization_path=1,
        moat=0,
    )

    winner, reason = select_protagonist([high_debt, clean_baseline])
    assert winner is not None
    assert winner.repo_id == "org/clean-baseline"


def test_weak_moat_stays_eligible():
    # Weak moat (0) ranks lower but remains eligible and can win if highest among viable
    viable = Scorecard(
        repo_id="org/utility",
        verdict="CANDIDATE",
        problem_severity=2,
        demand_evidence=2,
        differentiation=2,
        feasibility=2,
        productization_path=2,
        moat=0,  # Moat 0 is common for small utilities
    )
    winner, reason = select_protagonist([viable])
    assert winner is not None
    assert winner.repo_id == "org/utility"
    assert winner.moat.score == 0


# ---------------------------------------------------------------------------
# 5. Tie-Break Tests
# ---------------------------------------------------------------------------


def test_protagonist_tie_broken_by_repo_id_ascending():
    # Two candidates with identical scores and feasibility >= 2
    cand_z = Scorecard(
        repo_id="zeta-org/tool",
        verdict="CANDIDATE",
        problem_severity=2,
        demand_evidence=2,
        differentiation=2,
        feasibility=2,
        productization_path=2,
        moat=1,
    )
    cand_a = Scorecard(
        repo_id="alpha-org/tool",
        verdict="CANDIDATE",
        problem_severity=2,
        demand_evidence=2,
        differentiation=2,
        feasibility=2,
        productization_path=2,
        moat=1,
    )
    assert cand_z.total == cand_a.total

    winner, reason = select_protagonist([cand_z, cand_a])
    assert winner is not None
    # alpha-org/tool < zeta-org/tool alphabetically
    assert winner.repo_id == "alpha-org/tool"

    # Reversed input order gives same deterministic result
    winner2, _ = select_protagonist([cand_a, cand_z])
    assert winner2 is not None
    assert winner2.repo_id == "alpha-org/tool"


# ---------------------------------------------------------------------------
# 6. Protagonist Selection None Cases
# ---------------------------------------------------------------------------


def test_select_protagonist_none_cases():
    # Case 1: Empty input list
    res_empty = select_protagonist([])
    assert res_empty.protagonist is None
    assert "No candidates provided" in res_empty.reason
    assert not res_empty  # Evaluates to False in boolean context

    winner, reason = select_protagonist([])
    assert winner is None
    assert "No candidates provided" in reason

    # Case 2: All candidates are ineligible (ALREADY_EXISTS or NO_GO)
    ae = Scorecard(repo_id="org/exists", verdict="ALREADY_EXISTS", feasibility=3)
    nogo = Scorecard(repo_id="org/nogo", verdict="NO_GO", feasibility=3)
    res_ineligible = select_protagonist([ae, nogo])
    assert res_ineligible.protagonist is None
    assert "all repositories have ineligible verdicts" in res_ineligible.reason

    # Case 3: CANDIDATEs exist, but none meet feasibility >= 2
    low_feas1 = Scorecard(repo_id="org/stub1", verdict="CANDIDATE", feasibility=0)
    low_feas2 = Scorecard(repo_id="org/stub2", verdict="CANDIDATE", feasibility=1)
    res_feas = select_protagonist([low_feas1, low_feas2])
    assert res_feas.protagonist is None
    assert "feasibility threshold >= 2" in res_feas.reason


# ---------------------------------------------------------------------------
# 7. Reviewer Rework Requests Tests
# ---------------------------------------------------------------------------


def test_review_scorecard_missing_criterion():
    # Omitting moat and productization_path
    card = Scorecard(
        repo_id="org/incomplete",
        verdict="CANDIDATE",
        problem_severity=Criterion(score=2, evidence_ids=["ev1"], rationale="Pain"),
        demand_evidence=Criterion(score=2, evidence_ids=["ev1"], rationale="Demand"),
        differentiation=Criterion(score=2, evidence_ids=["ev1"], rationale="Diff"),
        feasibility=Criterion(score=2, evidence_ids=["ev1"], rationale="Feas"),
    )
    rework = review_scorecard(card)
    issues = [r.issue for r in rework]
    criteria = [r.criterion for r in rework]
    assert "missing_criterion" in issues
    assert "moat" in criteria
    assert "productization_path" in criteria


def test_review_scorecard_missing_evidence():
    evidence_set = {"known:ev1"}
    card = Scorecard(
        repo_id="org/no-evidence",
        verdict="CANDIDATE",
        problem_severity=Criterion(score=2, evidence_ids=[], rationale="No evidence cited"),
        demand_evidence=Criterion(score=2, evidence_ids=["unknown:ev99"], rationale="Cites ghost evidence"),
        differentiation=Criterion(score=2, evidence_ids=["known:ev1"], rationale="Valid diff"),
        feasibility=Criterion(score=2, evidence_ids=["known:ev1"], rationale="Valid feas"),
        productization_path=Criterion(score=1, evidence_ids=["known:ev1"], rationale="Valid prod"),
        moat=Criterion(score=0, evidence_ids=[], rationale="No moat"),
    )
    rework = review_scorecard(card, evidence_set=evidence_set)
    evidence_issues = [r for r in rework if r.issue == "missing_evidence"]
    rework_criteria = [r.criterion for r in evidence_issues]
    assert "problem_severity" in rework_criteria
    assert "demand_evidence" in rework_criteria


def test_review_scorecard_demand_citing_candidate_stars():
    card = Scorecard(
        repo_id="org/stars-demand",
        verdict="CANDIDATE",
        problem_severity=Criterion(score=2, evidence_ids=["ev1"], rationale="Valid pain"),
        demand_evidence=Criterion(score=2, evidence_ids=["ev1"], rationale="High demand based on repo stars"),
        differentiation=Criterion(score=2, evidence_ids=["ev1"], rationale="Diff"),
        feasibility=Criterion(score=2, evidence_ids=["ev1"], rationale="Feas"),
        productization_path=Criterion(score=1, evidence_ids=["ev1"], rationale="Prod"),
        moat=Criterion(score=0, evidence_ids=[], rationale="No moat"),
    )
    rework = review_scorecard(card)
    mismatches = [r for r in rework if r.issue == "score_anchor_mismatch" and r.criterion == "demand_evidence"]
    assert len(mismatches) >= 1
    assert "OUTSIDE the candidate repo" in mismatches[0].detail


def test_review_scorecard_moat_anchor_mismatch():
    # Moat score >= 2 must name one of the 7 Powers
    card = Scorecard(
        repo_id="org/moat-test",
        verdict="CANDIDATE",
        problem_severity=Criterion(score=2, evidence_ids=["ev1"], rationale="Pain"),
        demand_evidence=Criterion(score=2, evidence_ids=["ev1"], rationale="External demand"),
        differentiation=Criterion(score=2, evidence_ids=["ev1"], rationale="Diff"),
        feasibility=Criterion(score=2, evidence_ids=["ev1"], rationale="Feas"),
        productization_path=Criterion(score=1, evidence_ids=["ev1"], rationale="Prod"),
        moat=Criterion(score=2, evidence_ids=["ev1"], rationale="Very strong defense because we are fast"),
    )
    rework = review_scorecard(card)
    moat_issues = [r for r in rework if r.criterion == "moat" and r.issue == "score_anchor_mismatch"]
    assert len(moat_issues) >= 1
    assert "7 Powers" in moat_issues[0].detail

    # When naming a 7 Power (e.g. switching costs), it passes
    card.criteria["moat"].rationale = "High switching costs from local proprietary database schema"
    rework2 = review_scorecard(card)
    moat_issues2 = [r for r in rework2 if r.criterion == "moat" and r.issue == "score_anchor_mismatch"]
    assert len(moat_issues2) == 0


def test_review_scorecard_score_out_of_bounds():
    card = Scorecard(
        repo_id="org/bounds",
        verdict="CANDIDATE",
        problem_severity=Criterion(score=4, evidence_ids=["ev1"], rationale="Pain"),  # > 3
        demand_evidence=Criterion(score=2, evidence_ids=["ev1"], rationale="Demand"),
        differentiation=Criterion(score=2, evidence_ids=["ev1"], rationale="Diff"),
        feasibility=Criterion(score=2, evidence_ids=["ev1"], rationale="Feas"),
        productization_path=Criterion(score=1, evidence_ids=["ev1"], rationale="Prod"),
        moat=Criterion(score=0, evidence_ids=[], rationale="No moat"),
    )
    rework = review_scorecard(card)
    out_of_bounds = [r for r in rework if r.criterion == "problem_severity" and r.issue == "score_anchor_mismatch"]
    assert len(out_of_bounds) >= 1
    assert "outside the valid anchor range" in out_of_bounds[0].detail


def test_review_scorecard_fully_valid():
    evidence_set = {"candidate:org/valid", "demand:external-signal"}
    card = Scorecard(
        repo_id="org/valid",
        verdict="CANDIDATE",
        problem_severity=Criterion(score=2, evidence_ids=["candidate:org/valid"], rationale="Pain"),
        demand_evidence=Criterion(score=2, evidence_ids=["demand:external-signal"], rationale="Issue PR 80"),
        differentiation=Criterion(score=2, evidence_ids=["candidate:org/valid"], rationale="Missing in alt"),
        feasibility=Criterion(score=2, evidence_ids=["candidate:org/valid"], rationale="Tests pass"),
        productization_path=Criterion(score=2, evidence_ids=["candidate:org/valid"], rationale="CLI tool"),
        moat=Criterion(score=0, evidence_ids=[], rationale="No moat; utility tool"),
    )
    rework = review_scorecard(card, evidence_set=evidence_set)
    assert rework == []


# ---------------------------------------------------------------------------
# 8. Real Repository Case Integration (No Network)
# ---------------------------------------------------------------------------


def test_real_eval_repo_venture_assessment():
    from app.agents.equivalence import evidence_from_rows

    # SergioMendolia/ytkb is a real CANDIDATE in cases.json
    ytkb_case = _case("SergioMendolia/ytkb")
    evidence_items = evidence_from_rows(ytkb_case["candidate"], ytkb_case["prior_art"], ytkb_case["demand"])
    evidence_set = {str(item["id"]) for item in evidence_items}

    # Grounded scorecard for ytkb
    ytkb_card = Scorecard(
        repo_id="SergioMendolia/ytkb",
        verdict="CANDIDATE",
        problem_severity=Criterion(
            score=2,
            evidence_ids=["candidate:SergioMendolia/ytkb"],
            rationale="Syncing markdown bi-directionally with YouTrack KB is a manual struggle.",
        ),
        demand_evidence=Criterion(
            score=2,
            evidence_ids=["demand:foliant-pr-80"],
            rationale="Foliant PR 80 demonstrates external developer demand for YouTrack KB sync.",
        ),
        differentiation=Criterion(
            score=2,
            evidence_ids=["candidate:SergioMendolia/ytkb"],
            rationale="Maintained alternatives only export downward; ytkb pushes markdown edits back.",
        ),
        feasibility=Criterion(
            score=2,
            evidence_ids=["candidate:SergioMendolia/ytkb"],
            rationale="Python codebase with existing tests and clear CLI architecture.",
        ),
        productization_path=Criterion(
            score=1,
            evidence_ids=["candidate:SergioMendolia/ytkb"],
            rationale="Developer tool; hosted sync service for engineering teams is a potential path.",
        ),
        moat=Criterion(
            score=0,
            evidence_ids=[],
            rationale="No structural moat from 7 Powers; easily duplicated utility.",
        ),
    )

    # tharani-2006/Invoice-generator-application is ALREADY_EXISTS in cases.json
    invoice_case = _case("tharani-2006/Invoice-generator-application")
    invoice_card = Scorecard(
        repo_id="tharani-2006/Invoice-generator-application",
        verdict="ALREADY_EXISTS",
        problem_severity=2,
        demand_evidence=2,
        differentiation=0,
        feasibility=2,
        productization_path=1,
        moat=0,
    )

    # Clamping ytkb with verified evidence set retains scores
    ytkb_card.clamp(evidence_set)
    assert ytkb_card.demand_evidence.score == 2
    assert ytkb_card.is_eligible is True
    assert invoice_card.is_eligible is False

    # Protagonist selection chooses ytkb over invoice generator
    winner, reason = select_protagonist([invoice_card, ytkb_card], evidence_set=evidence_set)
    assert winner is not None
    assert winner.repo_id == "SergioMendolia/ytkb"
    assert winner.feasibility_score >= 2
    assert winner.total > 0

    # Reviewing ytkb scorecard finds zero rework requests
    rework = review_scorecard(ytkb_card, evidence_set=evidence_set)
    assert rework == []
