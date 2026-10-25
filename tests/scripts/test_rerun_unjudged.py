"""Rerun selection, merge, and backoff. No live Nightshift call."""
import json
import sys
from pathlib import Path

import pytest

from scripts import rerun_unjudged as rr


def test_infra_rows_are_selected_and_finished_judgements_are_not():
    assert rr.is_infra_review({"verdict": "UNJUDGED", "reason": "HTTP 502"})
    assert rr.is_infra_review({"verdict": "NEEDS_REVIEW", "reason": "Nightshift request failed: HTTP 502"})
    assert rr.is_infra_review({"verdict": "NEEDS_REVIEW", "reason": "LLM response was not valid JSON"})
    assert rr.is_infra_review({"verdict": "NEEDS_REVIEW", "reason": "the call timed out"})
    assert not rr.is_infra_review({"verdict": "NEEDS_REVIEW", "reason": "ALTERNATIVE_OVERLAP partial with tool"})
    assert not rr.is_infra_review({"verdict": "NEEDS_REVIEW", "reason": "MAINSTREAM_OVERLAP with a built-in feature"})
    assert not rr.is_infra_review({"verdict": "NEEDS_REVIEW", "reason": "quote 'alpha' not found in the repository text"})
    assert not rr.is_infra_review({"verdict": "ALREADY_EXISTS", "reason": "HTTP 502"})
    scores = {
        "ineligible": [
            {"repo_id": "a/exists", "verdict": "ALREADY_EXISTS", "reason": "full overlap"},
            {"repo_id": "b/review", "verdict": "NEEDS_REVIEW", "reason": "quote mismatch not found in readme"},
            {"repo_id": "c/down", "verdict": "UNJUDGED", "reason": "HTTP 502"},
            {"repo_id": "d/json", "verdict": "NEEDS_REVIEW", "reason": "LLM response was not valid JSON"},
        ]
    }
    assert [row["repo_id"] for row in rr.rows_to_rerun(scores)] == ["c/down", "d/json"]


def test_merge_keeps_finished_rows_and_replaces_only_the_rerun():
    original = {
        "candidates": [{"repo_id": "keep/me"}],
        "ineligible": [
            {"repo_id": "done/exists", "verdict": "ALREADY_EXISTS", "reason": "full"},
            {"repo_id": "bad/unjudged", "verdict": "UNJUDGED", "reason": "HTTP 502"},
        ],
        "protagonist": {"repo_id": "keep/me"},
    }
    replacements = {
        "bad/unjudged": {
            "candidates": [{"repo_id": "bad/unjudged"}],
            "ineligible": [],
        }
    }
    merged = rr.merge_rerun(original, replacements, {"source": "scores.json"})
    assert {row["repo_id"] for row in merged["candidates"]} == {"keep/me", "bad/unjudged"}
    assert [row["repo_id"] for row in merged["ineligible"]] == ["done/exists"]
    assert merged["protagonist"]["repo_id"] == "keep/me"
    assert "protagonist_note" in merged


def test_rerun_backs_off_and_retries_only_infrastructure_rows():
    scores = {
        "candidates": [],
        "ineligible": [
            {"repo_id": "a/ok", "verdict": "ALREADY_EXISTS", "reason": "full overlap"},
            {"repo_id": "b/u", "verdict": "UNJUDGED", "reason": "HTTP 502"},
            {"repo_id": "c/r", "verdict": "NEEDS_REVIEW", "reason": "Nightshift request HTTP 502"},
        ],
    }
    called: list[str] = []
    sleeps: list[float] = []

    def runner(repo_id: str) -> dict:
        called.append(repo_id)
        if repo_id == "c/r":
            return {
                "candidates": [],
                "ineligible": [{"repo_id": repo_id, "verdict": "NEEDS_REVIEW", "reason": "HTTP 502 still"}],
            }
        return {
            "candidates": [],
            "ineligible": [{"repo_id": repo_id, "verdict": "NO_GO", "reason": "not worth rescuing"}],
        }

    out = rr.rerun(
        scores,
        source="batch_scores.json",
        runner=runner,
        sleep=lambda seconds: sleeps.append(seconds),
        backoff_s=30,
        max_attempts=3,
    )
    assert "a/ok" not in called
    assert called == ["b/u", "c/r", "c/r", "c/r"]
    assert sleeps == [30, 60, 120]
    assert out["rerun"]["attempts"] == {"b/u": 1, "c/r": 3}
    assert out["rerun"]["still_infra"] == ["c/r"]
    verdicts = {row["repo_id"]: row["verdict"] for row in out["ineligible"]}
    assert verdicts["a/ok"] == "ALREADY_EXISTS"
    assert verdicts["b/u"] == "NO_GO"
    assert verdicts["c/r"] == "NEEDS_REVIEW"


def test_a_runner_error_keeps_the_old_row_and_continues():
    scores = {
        "candidates": [],
        "ineligible": [
            {"repo_id": "b/u", "verdict": "UNJUDGED", "reason": "HTTP 502"},
            {"repo_id": "c/u", "verdict": "UNJUDGED", "reason": "timed out"},
        ],
    }

    def runner(repo_id: str) -> dict:
        if repo_id == "b/u":
            raise RuntimeError("transport down")
        return {"candidates": [{"repo_id": repo_id}], "ineligible": []}

    out = rr.rerun(scores, source="s", runner=runner, sleep=lambda _s: None, backoff_s=1, max_attempts=1)
    kept = next(row for row in out["ineligible"] if row["repo_id"] == "b/u")
    assert kept["verdict"] == "UNJUDGED"
    assert "transport down" in kept["rerun_error"]
    assert [row["repo_id"] for row in out["candidates"]] == ["c/u"]


def test_missing_snapshot_row_is_not_invented(tmp_path: Path):
    snap = tmp_path / "snap"
    snap.mkdir()
    (snap / "discovery_candidates.csv").write_text("repo_id,description\nother/repo,d\n", encoding="utf-8")
    (snap / "prior_art.csv").write_text("repo_id\n", encoding="utf-8")
    (snap / "demand_signals.csv").write_text("repo_id\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        rr.write_subset(snap, "missing/repo", tmp_path / "dest")


def test_no_runner_does_not_rewrite_verdicts():
    scores = {"candidates": [], "ineligible": [{"repo_id": "b/u", "verdict": "UNJUDGED", "reason": "HTTP 502"}]}

    def explode(_seconds: float) -> None:
        raise AssertionError("sleep")

    out = rr.rerun(scores, source="s", runner=None, sleep=explode)
    assert out["ineligible"][0]["verdict"] == "UNJUDGED"
    assert out["rerun"]["live_ran"] is False


def test_markdown_shows_refusal_evidence():
    data = {
        "rerun": {"source": "in.json", "repos": ["o/r"], "backoff_seconds": 30, "still_infra": []},
        "candidates": [],
        "ineligible": [
            {
                "repo_id": "o/r",
                "verdict": "ALREADY_EXISTS",
                "reason": "full overlap",
                "decision_evidence": [
                    {"alternative": "other/tool", "overlap": "full", "quote": "does the same job", "reason": "same core job"}
                ],
            }
        ],
    }
    md = rr.render_rerun_md(data)
    assert "other/tool" in md and "full" in md and "does the same job" in md and "same core job" in md


def test_main_exits_without_rewriting_when_nightshift_is_not_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    scores = tmp_path / "scores.json"
    scores.write_text(
        json.dumps({"candidates": [], "ineligible": [{"repo_id": "a/b", "verdict": "UNJUDGED", "reason": "HTTP 502"}]}),
        encoding="utf-8",
    )
    out_json = tmp_path / "out.json"
    out_md = tmp_path / "out.md"
    monkeypatch.setattr(rr, "make_relevance_client", lambda _snapshot: object())
    monkeypatch.setattr("app.agents.relevance.is_client_configured", lambda _client: False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "rerun_unjudged",
            "--scores",
            str(scores),
            "--snapshot",
            str(tmp_path),
            "--output-json",
            str(out_json),
            "--output-md",
            str(out_md),
        ],
    )
    assert rr.main() == 2
    assert not out_json.exists()
    assert not out_md.exists()
