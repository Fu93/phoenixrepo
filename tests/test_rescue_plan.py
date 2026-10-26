"""Rescue plan: model drafts, code verifies, deterministic fallback, trace sealed."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agents.relevance import RelevanceClient
from app.rescue_plan import build_plan, organised, render_markdown
from app.trace_log import HackathonTrace
from scripts import rescue_plan as cli
from scripts.verify_trace import nemotron_report, verify

NEMO = "nvidia/nemotron-3-ultra-550b-a55b"
ROW = {
    "repo_id": "DSRegoitia/CalendarSyncProject",
    "verdict": "CANDIDATE",
    "total": 48.33,
    "criteria": {
        "differentiation": {"score": 2, "rationale": "Score 2: Pushes events into Google Calendar, which the display-only alternatives do not."},
        "feasibility": {"score": 1, "rationale": "Small codebase with a working sync script."},
    },
    "evidence": [
        {"id": "candidate:DSRegoitia/CalendarSyncProject", "url": "https://github.com/DSRegoitia/CalendarSyncProject",
         "text": "Sync events from a FullCalendar page into Google Calendar. Runs as a small Node service.", "content_hash": "abc"},
    ],
    "must_beat": [{"alternative": "fullcalendar/fullcalendar", "overlap": "partial", "quote": "FullCalendar can display events from a public Google Calendar.",
                   "reason": "did not block: overlap is partial, not the same job"}],
}
TREE_NO_TESTS = {"id": "repo:tree", "kind": "repo", "url": "", "text": "repository root: README.md, index.js, package.json",
                 "content_hash": "", "names": ["README.md", "index.js", "package.json"]}


def _client(tmp_path: Path, answer: dict) -> RelevanceClient:
    return RelevanceClient(cache_dir=tmp_path / "c", mock_handler=lambda p, s: json.dumps(answer), mock_served_model=NEMO)


def _answer() -> dict:
    return {
        "positioning": {"target_user": "teams that plan on a FullCalendar page", "job": "push those events into Google Calendar",
                        "gap": "FullCalendar only displays Google Calendar events", "evidence": ["candidate:DSRegoitia/CalendarSyncProject", "must_beat:fullcalendar/fullcalendar"]},
        "stages": [
            {"stage": "REVIVE", "items": [
                {"title": "Restore the sync against the current Google Calendar API", "why": "the stated job", "acceptance": "one event appears in Google Calendar",
                 "evidence": ["candidate:DSRegoitia/CalendarSyncProject"], "quote": "Sync events from a FullCalendar page into Google Calendar"},
                {"title": "Invented item", "why": "x", "acceptance": "x", "evidence": ["prior:nobody/here"], "quote": "anything"},
            ]},
            {"stage": "OPTIMISE", "items": [
                {"title": "Fake claim", "why": "x", "acceptance": "x", "evidence": ["candidate:DSRegoitia/CalendarSyncProject"], "quote": "10,000 users rely on it"},
            ]},
            {"stage": "UPGRADE", "items": [
                {"title": "Two-way sync", "why": "the gap", "acceptance": "an edit in Google Calendar shows on the page",
                 "evidence": ["must_beat:fullcalendar/fullcalendar"], "quote": "FullCalendar can display events from a public Google Calendar."},
            ]},
        ],
        "must_beat": [{"alternative": "fullcalendar/fullcalendar", "how_to_win": "write, not only display", "evidence": ["must_beat:fullcalendar/fullcalendar"]}],
        "risks": ["Google API quotas"],
    }


def test_model_plan_keeps_only_verified_items_and_adds_the_test_item(tmp_path: Path):
    trace = HackathonTrace("rescue-t", log_dir=tmp_path)
    plan = build_plan(ROW, client=_client(tmp_path, _answer()), trace=trace, tree=TREE_NO_TESTS)
    trace.seal()
    assert plan["drafted_by"] == "model" and plan["served_model"] == NEMO
    stages = {s["stage"]: s["items"] for s in plan["stages"]}
    assert [i["title"] for i in stages["REVIVE"]] == ["Add a runnable test command for the stated job", "Restore the sync against the current Google Calendar API"]
    assert stages["OPTIMISE"] == [] and [i["title"] for i in stages["UPGRADE"]] == ["Two-way sync"]
    reasons = {r["title"]: r["reason"] for r in plan["rejected_items"]}
    assert "unknown evidence" in reasons["Invented item"] and "not found verbatim" in reasons["Fake claim"]
    assert plan["organised"]["status"] == "needs_test_command"
    path = tmp_path / "trace-rescue-t.jsonl"
    assert verify(path)[0]
    report = nemotron_report(path)
    assert report["answers_used"] == 1 and report["answers_by_nemotron"] == 1
    actions = [json.loads(line)["action"] for line in path.read_text().splitlines()]
    assert actions[:1] == ["RESCUE_PLAN_INPUT"] and "LLM_CALL" in actions and "RESCUE_PLAN" in actions
    md = render_markdown(plan, run_id="rescue-t", run_hash="h")
    assert "Two-way sync" in md and "Dropped by the check" in md and "Fake claim" in md


def test_model_down_gives_a_labelled_deterministic_plan(tmp_path: Path):
    def boom(prompt, system):
        raise RuntimeError("502 from Nightshift")

    client = RelevanceClient(cache_dir=tmp_path / "c", mock_handler=boom)
    plan = build_plan(ROW, client=client, tree=None)
    assert plan["drafted_by"] == "deterministic"
    stages = {s["stage"]: s["items"] for s in plan["stages"]}
    assert stages["REVIVE"] and stages["UPGRADE"][0]["evidence"] == ["must_beat:fullcalendar/fullcalendar"]
    assert plan["organised"]["status"] == "unknown"


def test_only_a_candidate_gets_a_plan():
    with pytest.raises(ValueError):
        build_plan({**ROW, "verdict": "NEEDS_REVIEW"}, use_llm=False)


def test_test_path_in_root_means_verifiable():
    tree = {**TREE_NO_TESTS, "names": ["package.json", "test", "src"]}
    assert organised(tree)["status"] == "verifiable"


def test_cli_writes_plan_json_and_a_sealed_trace(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    scores = tmp_path / "scores.json"
    scores.write_text(json.dumps({"run_id": "g1", "run_hash": "rh", "candidates": [ROW],
                                  "ineligible": [{"repo_id": "x/y", "verdict": "ALREADY_EXISTS"}]}), encoding="utf-8")
    assert cli.main([str(scores), "--no-llm", "--offline", "--out", str(tmp_path / "out"), "--log-dir", str(tmp_path / "logs"), "--run-id", "rp"]) == 0
    plan = json.loads((tmp_path / "out" / "DSRegoitia__CalendarSyncProject.rescue.json").read_text())
    assert plan["gate1"]["run_hash"] == "rh" and plan["run_hash"] == verify(tmp_path / "logs" / "trace-rp.jsonl")[1]
    with pytest.raises(SystemExit, match="ALREADY_EXISTS"):
        cli.main([str(scores), "--repo", "x/y", "--no-llm", "--offline", "--out", str(tmp_path / "out"), "--log-dir", str(tmp_path / "logs")])


def test_revive_writes_the_plan_into_its_own_trace_and_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import app.rescue_plan as rp
    from scripts import revive

    monkeypatch.setattr(revive, "build_relevance_client", lambda **kw: _client(tmp_path, _answer()))
    monkeypatch.setattr(rp, "repo_tree", lambda repo_id, getter=None: TREE_NO_TESTS)
    scores = tmp_path / "scores.json"
    scores.write_text(json.dumps({"run_id": "g1", "run_hash": "rh", "candidates": [ROW]}), encoding="utf-8")
    trace = HackathonTrace("revive-t", log_dir=tmp_path)
    revive._write_rescue_plan(trace, "DSRegoitia/CalendarSyncProject", scores, tmp_path)
    plan = json.loads((tmp_path / "DSRegoitia__CalendarSyncProject.rescue.json").read_text())
    assert plan["drafted_by"] == "model" and plan["gate1"]["run_hash"] == "rh"
    revive._write_rescue_plan(trace, "DSRegoitia/CalendarSyncProject", tmp_path / "missing.json", tmp_path)
    trace.seal()
    lines = [json.loads(line) for line in (tmp_path / "trace-revive-t.jsonl").read_text().splitlines()]
    assert [line["status"] for line in lines if line["action"] == "RESCUE_PLAN"] == ["ok", "error"]
    assert verify(tmp_path / "trace-revive-t.jsonl")[0]
