"""The evidence report must read traces exactly as the real pipeline writes them (string summaries)."""
from pathlib import Path

from app.trace_log import HackathonTrace
from scripts.evidence_report import events_for_repo, load_events, render_repo_html

REPO = "acme/widget"


def _write_real_trace(tmp_path: Path) -> Path:
    trace = HackathonTrace("rt", log_dir=tmp_path)
    trace.event(agent_name="orchestrator", action="PROBLEM_STATEMENT", input_summary={"repo_id": REPO},
                output_summary={"problem": "p" * 50, "target_user": "devs", "category": "developer-tools",
                                "quotes": ["q" * 400, "r" * 400]}, summary_limit=20000)
    trace.event(agent_name="orchestrator", action="QUERY_GENERATION", input_summary={"repo_id": REPO},
                output_summary={"queries": ["a", "b"]})
    alts = [{"alternative_id": f"o/alt{i}", "url": f"https://github.com/o/alt{i}", "source": "github",
             "overlap": "full", "reason": "same job " * 20, "quotes": ["x" * 300]} for i in range(6)]
    trace.event(agent_name="orchestrator", action="ALTERNATIVES_VERIFIED",
                input_summary={"repo_id": REPO, "searched": 15}, output_summary={"alternatives": alts},
                summary_limit=20000)
    trace.event(agent_name="orchestrator", action="DISCOVERY_FILTER", input_summary={"repo_id": REPO},
                output_summary={"verdict": "ALREADY_EXISTS", "reason": "full overlap"})
    trace.seal()
    return tmp_path


def test_long_evidence_events_survive_as_valid_json(tmp_path):
    events = load_events(_write_real_trace(tmp_path), ["rt"], None)
    ev = events_for_repo(events, REPO)
    assert len(ev["alternatives"]) == 6
    assert ev["problem"]["category"] == "developer-tools"
    assert ev["queries"] == ["a", "b"]


def test_report_renders_from_real_trace(tmp_path):
    events = load_events(_write_real_trace(tmp_path), ["rt"], None)
    ev = events_for_repo(events, REPO)
    html = render_repo_html(REPO, "ALREADY_EXISTS", "full overlap", ev)
    assert "o/alt0" in html and "ALREADY_EXISTS" in html


def test_truncated_summaries_do_not_crash_the_report(tmp_path):
    trace = HackathonTrace("rt2", log_dir=tmp_path)
    trace.event(agent_name="orchestrator", action="ALTERNATIVES_VERIFIED", input_summary={"repo_id": REPO},
                output_summary={"alternatives": [{"alternative_id": "z" * 900}]})  # default limit cuts it
    trace.event(agent_name="orchestrator", action="QUERY_GENERATION", input_summary={"repo_id": REPO},
                output_summary="not json at all")
    trace.seal()
    ev = events_for_repo(load_events(tmp_path, ["rt2"], None), REPO)
    assert ev["alternatives"] == [] and ev["alternatives_unreadable"] is True
    render_repo_html(REPO, "NEEDS_REVIEW", "x", ev)
