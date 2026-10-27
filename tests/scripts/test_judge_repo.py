import json
from pathlib import Path

import pytest

from scripts import evidence_report as er
from scripts import judge_repo as jr


def _write_trace(path: Path, events):
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def _ev(action, repo_id, out, **inp):
    return {"action": action, "event": action, "input_summary": json.dumps({"repo_id": repo_id, **inp}), "output_summary": json.dumps(out), "line_hash": "h-" + action}


def test_report_escapes_html_and_shows_quotes(tmp_path):
    events = [
        _ev("PROBLEM_STATEMENT", "o/r", {"problem": "<script>x</script>", "target_user": "devs", "category": "sync", "quotes": ["sync notes"], "requested_model": "m", "served_model": "m2"}),
        _ev("ALTERNATIVES_VERIFIED", "o/r", {"alternatives": [{"alternative_id": "JetBrains/youtrack-cli", "url": "https://github.com/JetBrains/youtrack-cli", "source": "github", "overlap": "full", "reason": "does the same", "quotes": ["exports articles"]}]}, searched=3),
        {"action": "DISCOVERY_FILTER", "input_summary": json.dumps({"repo_id": "o/r"}), "output_summary": "{}", "line_hash": "x"},
        {"action": "RUN_SEAL", "input_summary": "{}", "output_summary": "{}", "line_hash": "SEALHASH"},
    ]
    _write_trace(tmp_path / "trace-run1.jsonl", events)
    loaded = er.load_events(tmp_path, run_ids=["run1"])
    ev = er.events_for_repo(loaded, "o/r")
    page = er.render_repo_html("o/r", "ALREADY_EXISTS", "full overlap", ev, run_id="run1")
    assert "<script>x</script>" not in page and "&lt;script&gt;" in page
    assert "exports articles" in page and "JetBrains/youtrack-cli" in page
    assert "SEALHASH" in page and "ALREADY_EXISTS" in page
    assert "Why this verdict" in page
    assert "Overlap" in page and "Verified quote" in page and "full" in page and "does the same" in page


def test_needs_review_report_shows_the_recorded_evidence():
    ev = {
        "decision_evidence": [
            {
                "alternative": "browser/voice",
                "overlap": "partial",
                "quote": "voice calls in the browser",
                "reason": "different form factor",
            }
        ]
    }
    page = er.render_repo_html("orangekame3/mirrormate", "NEEDS_REVIEW", "partial overlap", ev, run_id="run2")
    assert "Why this verdict" in page
    assert "browser/voice" in page and "partial" in page
    assert "voice calls in the browser" in page and "different form factor" in page


def test_report_without_trace_still_renders_verdict():
    page = er.render_repo_html("o/r", "UNJUDGED", "502", {})
    assert "UNJUDGED" in page and "No problem statement was recorded" in page


def test_render_all_writes_index(tmp_path):
    results = {"candidates": [], "ineligible": [{"repo_id": "a/b", "verdict": "NO_GO", "reason": "r"}]}
    files = er.render_all(results, [], tmp_path / "out")
    assert (tmp_path / "out" / "index.html").exists() and len(files) == 2


@pytest.mark.parametrize("u", ["https://github.com/a/b", "github.com/a/b/", "https://github.com/a/b.git", "a/b"])
def test_parse_repo_url(u):
    assert jr.parse_repo_url(u) == "a/b"


def test_parse_repo_url_rejects_other_hosts():
    with pytest.raises(jr.JudgeError):
        jr.parse_repo_url("https://example.com/a/b")


def test_build_row_requires_description():
    with pytest.raises(jr.JudgeError):
        jr.build_row({"repo": {"full_name": "a/b", "description": ""}, "readme": "", "paths": []}, "2026-10-24T00:00:00+00:00")


def _fake_getter(url, token, accept="application/vnd.github+json"):
    if url.endswith("/readme"):
        return "# Tool\n\nSync notes to a folder for developers.\n\n- Syncs notes every minute to disk\n- Exports markdown files with tags\n"
    if "/git/trees/" in url:
        return {"tree": [{"type": "blob", "path": p} for p in ("src/main.py", "src/sync.py", "src/util.py")]}
    return {"full_name": "a/b", "html_url": "https://github.com/a/b", "description": "Sync notes to a folder", "default_branch": "main", "created_at": "2025-01-01T00:00:00Z", "pushed_at": "2025-02-01T00:00:00Z", "stargazers_count": 3, "forks_count": 0, "open_issues_count": 0, "license": {"spdx_id": "MIT"}}


def test_judge_end_to_end_with_fake_scorer(tmp_path):
    seen = {}

    def scorer(snapshot, output_json, output_md, use_llm, run_id, log_dir, command_str, evidence_source=None):
        import csv

        seen["evidence_source"] = evidence_source

        rows = list(csv.DictReader((Path(snapshot) / "discovery_candidates.csv").open(encoding="utf-8")))
        seen["rows"] = rows
        seen["files"] = sorted(p.name for p in Path(snapshot).iterdir())
        return {"candidates": [], "ineligible": [{"repo_id": "a/b", "verdict": "ALREADY_EXISTS", "reason": "overlap"}], "run_hash": "RH"}

    res = jr.judge("https://github.com/a/b", out_dir=tmp_path / "o", run_id="jt", log_dir=tmp_path, getter=_fake_getter, scorer=scorer)
    assert res["verdict"] == "ALREADY_EXISTS" and res["run_hash"] == "RH"
    assert seen["rows"][0]["repo_id"] == "a/b" and "prior_art.csv" in seen["files"]
    # A repository fetched live is not a row in the curated Zetaris views; asking Zetaris for it
    # would turn every single-repo judgement into NEEDS_REVIEW.
    assert seen["evidence_source"] == "csv"
    assert Path(res["report"]).read_text(encoding="utf-8").count("ALREADY_EXISTS") >= 1


def _settings_with_token(token):
    from config.settings import Settings

    return Settings(meterless_runtime_token=token, _env_file=None)


def test_judge_endpoint_maps_errors(monkeypatch):
    from fastapi.testclient import TestClient

    from api import run as api_run
    from api.run import app

    def boom(url, use_llm=True):
        raise jr.JudgeError("no description")

    monkeypatch.setattr(jr, "judge", boom)
    monkeypatch.setattr(api_run, "get_settings", lambda: _settings_with_token("rt-real-token"))
    r = TestClient(app).post("/judge", json={"url": "a/b"})
    assert r.status_code == 422 and r.json()["error"]["type"] == "CannotJudge"


@pytest.mark.parametrize("token", [None, "", "rt-local", "your-nightshift-runtime-token"])
def test_judge_endpoint_returns_503_and_no_verdict_without_a_token(monkeypatch, token):
    from fastapi.testclient import TestClient

    from api import run as api_run
    from api.run import app

    called = {"n": 0}

    def boom(url, use_llm=True):
        called["n"] += 1
        return {"verdict": "NO_GO", "answers_used": 0}

    monkeypatch.setattr(jr, "judge", boom)
    monkeypatch.setattr(api_run, "get_settings", lambda: _settings_with_token(token))
    r = TestClient(app).post("/judge", json={"url": "Fu93/hygen", "use_llm": False})
    body = r.json()
    assert r.status_code == 503
    assert body["error"]["type"] == "ModelJudgingNotConfigured"
    assert "No verdict was produced" in body["error"]["message"]
    assert "verdict" not in body
    assert called["n"] == 0