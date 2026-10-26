"""Replay UI: a judge can play committed records with no token, network, or Docker."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.run import app
from app.agents.rescue import from_candidate_row
from app.replay_ui import step_from_event

ROOT = Path(__file__).resolve().parents[2]
CLIENT = TestClient(app)


def test_ui_is_static_html_without_a_cdn():
    response = CLIENT.get("/ui")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    page = response.text
    assert 'id="timeline"' in page
    assert 'id="verdict"' in page
    assert "MODEL_MISMATCH" in page
    assert "SOURCE_ERROR" in page
    assert "NOT_ATTEMPTED_GATE1" in page
    assert "<script" in page and "src=" not in page.split("<script", 1)[1].split(">", 1)[0]
    lowered = page.lower()
    assert "cdn." not in lowered
    assert "googleapis" not in lowered
    assert "unpkg.com" not in lowered
    assert "jsdelivr" not in lowered


def test_replay_catalog_lists_recorded_cases_and_the_gaps():
    body = CLIENT.get("/api/replays").json()
    ids = [row["id"] for row in body["replays"]]
    assert "invoice-already-exists" in ids
    assert "invoice-gate2-sample" in ids
    assert "hygen-already-exists-quote" in ids
    assert "mirrormate-human-label" in ids
    missing = json.dumps(body["missing"])
    assert "NEEDS_REVIEW" in missing
    assert "jsonl" in missing or "trace" in missing


def test_invoice_replay_returns_steps_and_numbers_from_repo_files():
    body = CLIENT.get("/api/replays/invoice-already-exists").json()
    assert body["trace_class"] == "SAMPLE"
    assert body["data_class"] == "RECORDED"
    assert len(body["steps"]) >= 2
    assert body["verification"]["ok"] is True
    verdict = body["verdict"]
    assert verdict["verdict"] == "ALREADY_EXISTS"
    scores = (ROOT / "docs/revival/venture_scores.json").read_text(encoding="utf-8")
    prior = (ROOT / "data/real/prior_art.csv").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert verdict["reason"] in scores
    assert verdict["quote"] in prior
    assert "get a real PDF" in verdict["quote"]
    assert verdict["quote_source_url"] == "https://github.com/al1abb/invoify"
    assert verdict["gate2"]["rule_quote"] in readme
    assert verdict["gate2"]["historical_trace"] is False
    assert "NOT_ATTEMPTED_GATE1" in verdict["gate2"]["rule_quote"]

    discovery = (ROOT / "data/real/discovery_candidates.csv").read_text(encoding="utf-8")
    assert verdict["rescue"]["inputs"]["pushed_at"] in discovery
    as_of = verdict["rescue"]["as_of"]
    from datetime import date

    expected = from_candidate_row(
        {
            "stargazers_count": verdict["rescue"]["inputs"]["stargazers_count"],
            "forks_count": verdict["rescue"]["inputs"]["forks_count"],
            "open_issues": verdict["rescue"]["inputs"]["open_issues"],
            "pushed_at": verdict["rescue"]["inputs"]["pushed_at"],
        },
        as_of=date.fromisoformat(as_of),
    )
    assert verdict["rescue"]["score"] == expected["score"]
    assert verdict["rescue"]["components"] == expected["components"]
    assert verdict["rescue"]["in_source_score_file"] is False

    for step in body["steps"]:
        assert step["agent"]
        assert step["action"]
        assert step["status"]
        assert "seconds" in step
        assert isinstance(step["flags"], list)
        for field in ("requested_model", "served_model"):
            if step[field]:
                assert step[field] in scores or step[field] in prior


def test_hygen_quote_and_mirrormate_are_labeled_human_records():
    labels = json.loads((ROOT / "data/eval/gate1_labels.json").read_text(encoding="utf-8"))
    hygen_case = next(case for case in labels["cases"] if case["repo_id"] == "Fu93/hygen")
    hygen_quote = next(item["quote"] for item in hygen_case["evidence"] if item["role"] == "alternative")
    hygen = CLIENT.get("/api/replays/hygen-already-exists-quote").json()
    assert hygen["data_class"] == "HUMAN_LABEL"
    assert hygen["verdict"]["verdict"] == "ALREADY_EXISTS"
    assert hygen["verdict"]["quote"] == hygen_quote
    assert hygen["verdict"]["quote_source_url"].startswith("https://")
    assert len(hygen["steps"]) >= 1

    mirror_case = next(case for case in labels["cases"] if case["repo_id"] == "orangekame3/mirrormate")
    mirror = CLIENT.get("/api/replays/mirrormate-human-label").json()
    assert mirror["data_class"] == "HUMAN_LABEL"
    assert mirror["verdict"]["verdict"] != "NEEDS_REVIEW"
    assert mirror["verdict"]["acceptable"] == mirror_case["acceptable"]
    assert "NEEDS_REVIEW" in mirror["verdict"]["acceptable"]
    assert mirror["verdict"]["quote"] == mirror_case["evidence"][0]["quote"]
    assert mirror["model_verdict_recorded"] is False


def test_gate2_sample_shows_not_attempted_and_is_labeled_sample():
    body = CLIENT.get("/api/replays/invoice-gate2-sample").json()
    assert body["trace_class"] == "SAMPLE"
    assert body["verdict"]["verdict"] == "ALREADY_EXISTS"
    gate2 = body["verdict"]["gate2"]
    assert gate2["state"] == "NOT_ATTEMPTED_GATE1"
    assert gate2["historical_trace"] is False
    assert "SAMPLE" in body["banner"]
    actions = [step["action"] for step in body["steps"]]
    assert "REVIVE_BLOCKED_BY_GATE1" in actions
    blocked = next(step for step in body["steps"] if step["action"] == "REVIVE_BLOCKED_BY_GATE1")
    assert blocked["status"] == "blocked"
    assert blocked["seconds"] is None
    assert body["verification"]["ok"] is True


def test_tamper_fails_verification_and_leaves_the_file(tmp_path):
    replay_id = "invoice-already-exists"
    trace = ROOT / "data" / "replays" / replay_id / "trace.jsonl"
    before = trace.read_bytes()
    response = CLIENT.post(f"/api/replays/{replay_id}/tamper")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "line_hash" in body["detail"] or "prev_hash" in body["detail"] or "JSON" in body["detail"]
    assert trace.read_bytes() == before
    again = CLIENT.get(f"/api/replays/{replay_id}").json()
    assert again["verification"]["ok"] is True


def test_live_judge_is_disabled_without_a_nightshift_token():
    body = CLIENT.get("/api/live").json()
    assert body["enabled"] is False
    assert "METERLESS_RUNTIME_TOKEN" in body["reason"]
    dumped = json.dumps(body)
    assert "nvapi-" not in dumped
    assert "ghp_" not in dumped


def test_live_judge_enables_without_echoing_the_token(monkeypatch):
    from config.settings import Settings

    from api import run as api_run

    secret = "nightshift-test-token-value"
    monkeypatch.setattr(api_run, "get_settings", lambda: Settings(meterless_runtime_token=secret, _env_file=None))
    body = CLIENT.get("/api/live").json()
    assert body["enabled"] is True
    assert secret not in json.dumps(body)


def test_step_parser_surfaces_mismatch_source_error_and_seconds():
    event = {
        "agent_name": "orchestrator",
        "action": "PRIOR_ART_SEARCH",
        "status": "ok",
        "requested_model": "nvidia/nemotron-3-super-120b-a12b",
        "served_model": "gemini-3.1-flash-lite",
        "timestamp": "2026-10-22T22:09:00+00:00",
        "input_summary": "{}",
        "output_summary": json.dumps(
            {"source_status": {"github": "ok", "pypi": "SOURCE_ERROR"}, "latency_ms": 1500, "flags": []}
        ),
    }
    step = step_from_event(event, 3)
    assert step["agent"] == "orchestrator"
    assert step["requested_model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert step["served_model"] == "gemini-3.1-flash-lite"
    assert step["seconds"] == 1.5
    assert "MODEL_MISMATCH" in step["flags"]
    assert "SOURCE_ERROR" in step["flags"]
    assert step["index"] == 3


def test_dockerfile_copies_the_ui_and_replays():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "app/ui" in text
    assert "data/replays" in text
