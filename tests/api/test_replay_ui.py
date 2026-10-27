"""Replay UI: a judge can play committed records with no token, network, or Docker."""

import json
from pathlib import Path

import pytest

from fastapi.testclient import TestClient

from api.run import app
from app.agents.rescue import from_candidate_row
from app.replay_ui import model_summary, step_from_event

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


def test_ui_documents_the_real_trace_import_and_keeps_sample_visible():
    page = CLIENT.get("/ui").text
    assert "SAMPLE" in page
    assert "python scripts/import_replay.py logs/trace-<run_id>.jsonl --verdict path/to/scores.json" in page
    assert 'id="served-models"' in page
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "python scripts/import_replay.py logs/trace-<run_id>.jsonl --verdict path/to/scores.json" in readme


def test_same_family_serve_is_not_a_mismatch_and_the_line_flag_is_kept():
    same_family = step_from_event(
        {
            "agent_name": "orchestrator",
            "action": "LLM_CALL",
            "status": "ok",
            "requested_model": "nvidia/nemotron-3-super-120b-a12b",
            "served_model": "nvidia/nemotron-3-ultra-550b-a55b",
            "input_summary": "{}",
            "output_summary": "{}",
        },
        1,
    )
    assert same_family["flags"] == []
    assert same_family["llm_line"] is True
    flagged = step_from_event(
        {
            "agent_name": "orchestrator",
            "action": "LLM_CALL",
            "status": "ok",
            "requested_model": "nvidia/nemotron-3-super-120b-a12b",
            "served_model": "",
            "flags": ["MODEL_MISMATCH"],
            "input_summary": "{}",
            "output_summary": "{}",
        },
        2,
    )
    assert flagged["flags"] == ["MODEL_MISMATCH"]
    assert flagged["served_model"] is None
    assert flagged["llm_line"] is True


def test_import_replay_copies_the_trace_and_the_page_shows_the_model_table(tmp_path, monkeypatch):
    from app.trace_log import HackathonTrace
    from scripts.import_replay import ImportError as ReplayImportError
    from scripts.import_replay import import_replay

    trace = HackathonTrace("live-1", log_dir=tmp_path)
    trace.event(
        agent_name="orchestrator",
        action="LLM_CALL",
        input_summary={"repo_id": "acme/widget", "step": "coverage"},
        output_summary={"requested_model": "nvidia/nemotron-3-super-120b-a12b", "served_model": "gemini-3.1-flash-lite"},
        llm={"requested_model": "nvidia/nemotron-3-super-120b-a12b", "served_model": "gemini-3.1-flash-lite"},
        status="ok",
    )
    trace.event(
        agent_name="orchestrator",
        action="LLM_CALL",
        input_summary={"repo_id": "acme/widget", "step": "again"},
        llm={"requested_model": "nvidia/nemotron-3-super-120b-a12b", "served_model": "nvidia/nemotron-3-super-120b-a12b"},
        status="ok",
    )
    trace.seal()
    scores = {
        "run_id": "live-1",
        "run_hash": trace.run_hash,
        "llm_used": True,
        "ineligible": [
            {
                "repo_id": "acme/widget",
                "verdict": "NEEDS_REVIEW",
                "reason": "partial overlap",
                "decision_evidence": [
                    {
                        "alternative": "other/tool",
                        "url": "https://github.com/other/tool",
                        "overlap": "partial",
                        "quote": "a maintained tool",
                    }
                ],
            },
            {"repo_id": "other/one", "verdict": "NO_GO", "reason": "toy"},
        ],
    }
    verdict_path = tmp_path / "scores.json"
    verdict_path.write_text(json.dumps(scores), encoding="utf-8")
    replays = tmp_path / "replays"
    replays.mkdir()
    (replays / "manifest.json").write_text(json.dumps({"replays": [], "missing": []}), encoding="utf-8")
    imported = import_replay(trace.path, verdict_path, replays_root=replays, repo="acme/widget")
    assert imported["id"] == "acme-widget"
    copied = (replays / "acme-widget" / "trace.jsonl").read_bytes()
    assert copied == trace.path.read_bytes()
    manifest = json.loads((replays / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["replays"][0]["id"] == "acme-widget"
    assert manifest["replays"][0]["trace_class"] == "RECORDED"
    monkeypatch.setattr("app.replay_ui.REPLAYS_ROOT", replays)
    body = CLIENT.get("/api/replays/acme-widget").json()
    assert body["trace_class"] == "RECORDED"
    assert body["verdict"]["verdict"] == "NEEDS_REVIEW"
    assert body["verdict"]["quote"] == "a maintained tool"
    assert body["models"]["model_calls"] == {
        "gemini-3.1-flash-lite": 1,
        "nvidia/nemotron-3-super-120b-a12b": 1,
    }
    assert body["models"]["model_mismatch_count"] == 1
    mismatched = next(step for step in body["steps"] if step["served_model"] == "gemini-3.1-flash-lite")
    assert "MODEL_MISMATCH" in mismatched["flags"]
    assert body["verification"]["ok"] is True
    with pytest.raises(ReplayImportError):
        import_replay(trace.path, verdict_path, replays_root=replays)
    with pytest.raises(ReplayImportError):
        import_replay(trace.path, verdict_path, replays_root=replays, repo="")


def test_error_line_is_not_relabelled_as_a_model_mismatch():
    failed = step_from_event(
        {
            "agent_name": "orchestrator",
            "action": "LLM_CALL",
            "status": "error",
            "requested_model": "nvidia/nemotron-3-super-120b-a12b",
            "served_model": "",
            "flags": ["CALL_FAILED"],
            "input_summary": "{}",
            "output_summary": "{}",
        },
        1,
    )
    assert "MODEL_MISMATCH" not in failed["flags"]
    assert "CALL_FAILED" in failed["flags"]
    # A scored v3 line stored MODEL_MISMATCH on the failure. The hash is unchanged, so the flag stays.
    historical = step_from_event(
        {
            "agent_name": "orchestrator",
            "action": "LLM_CALL",
            "status": "error",
            "requested_model": "nvidia/nemotron-3-super-120b-a12b",
            "served_model": "",
            "flags": ["MODEL_MISMATCH"],
            "input_summary": "{}",
            "output_summary": "{}",
        },
        2,
    )
    assert "MODEL_MISMATCH" in historical["flags"]
    summary = model_summary([failed, historical])
    assert summary["call_failed_count"] == 1
    assert summary["model_mismatch_count"] == 1


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


def test_import_replay_refuses_a_trace_that_contains_a_secret(tmp_path, monkeypatch):
    from app.trace_log import HackathonTrace
    from scripts.import_replay import ImportError as ReplayImportError
    from scripts.import_replay import import_replay

    monkeypatch.setenv("GITHUB_TOKEN", "plain-secret-value-123")
    # A trace written by older code (before script traces redacted) can hold the raw value.
    trace = HackathonTrace("old-1", log_dir=tmp_path)
    trace.event(agent_name="toolbox", action="TOOL_CALL", output_summary="header plain-secret-value-123", status="ok")
    trace.seal()
    verdict_path = tmp_path / "scores.json"
    verdict_path.write_text(json.dumps({"repo_id": "acme/widget", "verdict": "ALREADY_EXISTS"}), encoding="utf-8")
    replays = tmp_path / "replays"
    replays.mkdir()
    (replays / "manifest.json").write_text(json.dumps({"replays": [], "missing": []}), encoding="utf-8")
    with pytest.raises(ReplayImportError, match="credential"):
        import_replay(trace.path, verdict_path, replays_root=replays)
    assert not (replays / "acme-widget").exists()


def test_verdict_card_carries_the_nemotron_reasoning_and_share(tmp_path, monkeypatch):
    from app.trace_log import HackathonTrace
    from scripts.import_replay import import_replay

    trace = HackathonTrace("live-nemo", log_dir=tmp_path)
    trace.event(
        agent_name="orchestrator", action="LLM_CALL",
        input_summary={"repo_id": "acme/widget", "step": "alternative"},
        output_summary={"requested_model": "nvidia/nemotron-3.5-lightning-30b-a3b", "served_model": "nvidia/nemotron-3.5-lightning-30b-a3b"},
        llm={"requested_model": "nvidia/nemotron-3.5-lightning-30b-a3b", "served_model": "nvidia/nemotron-3.5-lightning-30b-a3b"},
    )
    trace.event(
        agent_name="orchestrator", action="LLM_CALL",
        input_summary={"repo_id": "acme/widget", "step": "discovery"},
        output_summary={"served_model": "gemini-3.1-flash-lite", "reasoning": "discarded answer"},
        llm={"requested_model": "nvidia/nemotron-3-ultra-550b-a55b", "served_model": "gemini-3.1-flash-lite"},
        status="rejected",
    )
    trace.event(
        agent_name="orchestrator", action="LLM_CALL",
        input_summary={"repo_id": "acme/widget", "step": "discovery"},
        output_summary={"served_model": "nvidia/nemotron-3-ultra-550b-a55b", "reasoning": "Docs page does the same job.", "tier": "deep"},
        llm={"requested_model": "nvidia/nemotron-3-ultra-550b-a55b", "served_model": "nvidia/nemotron-3-ultra-550b-a55b"},
        summary_limit=4000,
    )
    trace.seal()
    verdict_path = tmp_path / "v.json"
    verdict_path.write_text(json.dumps({"repo_id": "acme/widget", "verdict": "ALREADY_EXISTS", "reason": "full overlap"}), encoding="utf-8")
    replays = tmp_path / "replays"
    replays.mkdir()
    (replays / "manifest.json").write_text(json.dumps({"replays": [], "missing": []}), encoding="utf-8")
    import_replay(trace.path, verdict_path, replays_root=replays)
    monkeypatch.setattr("app.replay_ui.REPLAYS_ROOT", replays)
    body = CLIENT.get("/api/replays/acme-widget").json()
    thought = body["verdict_reasoning"]
    assert thought["text"] == "Docs page does the same job."
    assert thought["served_model"] == "nvidia/nemotron-3-ultra-550b-a55b" and thought["nemotron"] is True
    assert body["models"]["answers_used"] == 2 and body["models"]["answers_by_nemotron"] == 2
    assert body["models"]["discarded_by_policy"] == 1
    page = CLIENT.get("/ui").text
    assert "Reasoning field on this trace line" in page and "answers by NVIDIA Nemotron" in page
    assert "Nemotron's reasoning" not in page
    assert "The hash this page verified is that run hash." in page
    assert "The hash this page verified is a different hash." in page
    assert "This page's seal is a different hash." not in page
