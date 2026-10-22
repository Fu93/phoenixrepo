import json
from pathlib import Path

from scripts.demo_summary import render_summary
from scripts.render_trace import render_html, render_markdown


def test_summary_lists_verdicts_and_trace_links():
    text = render_summary(
        checks=[{"name": "nightshift_models", "result": "200"}, {"name": "zetaris_select_1", "result": "unreachable"}],
        runs=[
            {
                "repository": "golang/dep",
                "decision": "NO_GO",
                "state": "NO_GO",
                "http_status": "200",
                "reason": "similar_repo_count is 145483",
                "log_file": "logs/trace-abc.jsonl",
            }
        ],
    )
    assert "nightshift_models" in text
    assert "unreachable" in text
    assert "golang/dep" in text
    assert "NO_GO" in text
    assert "[logs/trace-abc.jsonl](logs/trace-abc.jsonl)" in text


def test_summary_with_no_runs_says_so():
    text = render_summary(checks=[{"name": "docker", "result": "unreachable"}], runs=[])
    assert "No `/run` calls were made." in text


def test_render_trace_timeline_escapes_html(tmp_path):
    event = {
        "timestamp": "2026-10-22T00:00:00+00:00",
        "agent_name": "validator",
        "action": "LLM_JUDGE",
        "status": "reject",
        "input_summary": '{"requested_model":"meterless/auto"}',
        "output_summary": "<script>alert(1)</script>",
        "served_model": "openrouter/poolside/laguna-s-2.1:free",
    }
    path = tmp_path / "trace.jsonl"
    path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    from scripts.render_trace import load_events

    events = load_events(path)
    markdown = render_markdown(events, title="Trace demo")
    html = render_html(events, title="Trace <demo>")
    assert "LLM_JUDGE" in markdown
    assert "meterless/auto" in markdown
    assert "served_model" in markdown
    assert "openrouter/poolside/laguna-s-2.1:free" in markdown
    assert "openrouter/poolside/laguna-s-2.1:free" in html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "Trace &lt;demo&gt;" in html
    assert Path(path).exists()
