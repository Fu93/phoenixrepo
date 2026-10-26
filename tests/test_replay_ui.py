"""Replay UI: committed traces and reports, no network and no tokens."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.run import app

ROOT = Path(__file__).resolve().parents[1]
LABELS = ROOT / "data" / "eval" / "gate1_labels.json"
SCORES = ROOT / "docs" / "revival" / "venture_scores.json"
MISSING = "not recorded"


def _labels():
    return json.loads(LABELS.read_text(encoding="utf-8"))


def _scores():
    return json.loads(SCORES.read_text(encoding="utf-8"))


def _client():
    return TestClient(app)


def test_ui_page_is_self_contained():
    response = _client().get("/ui")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    page = response.text
    for label in ("Live judge", "Verify trace", "Tamper one character", "Play", "Pause"):
        assert label in page
    assert "cdn" not in page.lower()
    assert "googleapis" not in page.lower()
    assert "unpkg" not in page.lower()
    assert "jsdelivr" not in page.lower()
    assert "<script src=" not in page.lower()
    assert "<link rel=\"stylesheet\" href=" not in page.lower()


def test_replay_list_names_the_committed_recordings():
    payload = _client().get("/ui/runs").json()
    ids = {row["id"] for row in payload["runs"]}
    scores = _scores()
    labels = _labels()
    assert scores["run_id"] in ids
    assert f"gate1-labels-{labels['fetched_at']}" in ids
    by_id = {row["id"]: row for row in payload["runs"]}
    venture = by_id[scores["run_id"]]
    assert venture["source"].endswith("docs/revival/venture_scores.json")
    assert Path(ROOT / venture["trace"]).is_file()
    audit = by_id[f"gate1-labels-{labels['fetched_at']}"]
    assert audit["source"].endswith("data/eval/gate1_labels.json")
    assert Path(ROOT / audit["trace"]).is_file()


def test_audit_trace_shows_invoiceplane_numbers_from_the_file():
    labels = _labels()
    invoice = next(case for case in labels["cases"] if case["repo_id"] == "tharani-2006/Invoice-generator-application")
    blocker = invoice["audit"]["blocker"]
    assert blocker["stars"] == 3258
    assert blocker["pushed_at"] == "2026-10-25"
    run_id = f"gate1-labels-{labels['fetched_at']}"
    detail = _client().get(f"/ui/runs/{run_id}").json()
    step = next(item for item in detail["steps"] if item["event"] == invoice["repo_id"])
    card = step["card"]
    alternative = card["alternatives"][0]
    assert alternative["name"] == blocker["alternative_id"]
    assert alternative["stars"] == blocker["stars"]
    assert alternative["last_push"] == blocker["pushed_at"]
    assert alternative["url"] == blocker["url"]
    quote = next(item["quote"] for item in invoice["evidence"] if item["role"] == "alternative")
    assert quote in card["quotes"]
    assert card["verified"] == "substring of stored text"
    assert card["verdict"] == MISSING
    assert card["rescue_score"] == MISSING
    assert card["rescue_components"] == MISSING
    assert card["source_error"] == MISSING
    assert step["agent"] == MISSING
    assert step["requested_model"] == MISSING
    assert step["served_model"] == MISSING
    assert step["duration"] == MISSING
    assert detail["note"] == labels["note"]


def test_sparse_audit_row_does_not_invent_stars_or_a_push_date():
    labels = _labels()
    case = next(item for item in labels["cases"] if item["repo_id"] == "ri-char/pwatch")
    assert "stars" not in case["audit"]["blocker"]
    assert "pushed_at" not in case["audit"]["blocker"]
    run_id = f"gate1-labels-{labels['fetched_at']}"
    detail = _client().get(f"/ui/runs/{run_id}").json()
    step = next(item for item in detail["steps"] if item["event"] == case["repo_id"])
    alternative = step["card"]["alternatives"][0]
    assert alternative["name"] == case["audit"]["blocker"]["alternative_id"]
    assert alternative["url"] == case["audit"]["blocker"]["url"]
    assert alternative["stars"] == MISSING
    assert alternative["last_push"] == MISSING


def test_venture_score_file_keeps_its_recorded_verdict_and_omits_rescue():
    scores = _scores()
    assert scores["run_hash"] == "322ce42a3527a7c4a719853e61b5c2842833653fb5384f0726ed83856d475551"
    invoice = next(row for row in scores["ineligible"] if row["verdict"] == "ALREADY_EXISTS")
    detail = _client().get(f"/ui/runs/{scores['run_id']}").json()
    assert detail["recorded_run_hash"] == scores["run_hash"]
    assert detail["llm_used"] is False
    step = next(item for item in detail["steps"] if item.get("repo_id") == invoice["repo_id"])
    card = step["card"]
    assert card["verdict"] == "ALREADY_EXISTS"
    assert card["reason"] == invoice["reason"]
    assert card["alternatives"][0]["name"] == "al1abb/invoify"
    assert card["alternatives"][0]["url"] == "https://github.com/al1abb/invoify"
    assert card["alternatives"][0]["last_push"] == "2026-08-24T02:55:34Z"
    assert card["alternatives"][0]["stars"] == MISSING
    assert card["quotes"] == MISSING
    assert card["verified"] == MISSING
    assert card["rescue_score"] == MISSING
    assert card["source_error"] == MISSING
    assert step["duration"] == MISSING
    assert step["requested_model"] == MISSING
    assert step["served_model"] == MISSING
    ytkb = next(item for item in detail["steps"] if item.get("repo_id") == "SergioMendolia/ytkb" and item["card"]["verdict"] == "CANDIDATE")
    assert ytkb["card"]["rescue_score"] == MISSING
    assert ytkb["card"]["alternatives"] == MISSING


def test_committed_trace_text_is_the_seal_of_the_source_file():
    from app.replay import sealed_trace_text

    scores = _scores()
    detail = _client().get(f"/ui/runs/{scores['run_id']}").json()
    committed = (ROOT / detail["trace"]).read_text(encoding="utf-8")
    assert committed == sealed_trace_text(scores["run_id"])
    assert scores["run_hash"] in committed
    labels = _labels()
    audit_id = f"gate1-labels-{labels['fetched_at']}"
    audit = _client().get(f"/ui/runs/{audit_id}").json()
    assert (ROOT / audit["trace"]).read_text(encoding="utf-8") == sealed_trace_text(audit_id)


def test_verify_passes_and_tamper_fails_without_touching_the_file():
    scores = _scores()
    detail = _client().get(f"/ui/runs/{scores['run_id']}").json()
    path = ROOT / detail["trace"]
    before = path.read_bytes()
    client = _client()
    passed = client.post(f"/ui/runs/{scores['run_id']}/verify").json()
    assert passed["ok"] is True
    assert passed["result"] == "PASS"
    assert passed["detail"] == detail["trace_seal"]
    tampered = client.post(f"/ui/runs/{scores['run_id']}/tamper").json()
    assert tampered["ok"] is False
    assert tampered["result"] == "FAIL"
    assert "line_hash" in tampered["detail"]
    assert len(tampered["trace"]) == len(before.decode("utf-8"))
    assert sum(a != b for a, b in zip(before.decode("utf-8"), tampered["trace"])) == 1
    checked = client.post(f"/ui/runs/{scores['run_id']}/verify", json={"trace": tampered["trace"]}).json()
    assert checked["result"] == "FAIL"
    assert path.read_bytes() == before
    again = client.post(f"/ui/runs/{scores['run_id']}/verify").json()
    assert again["result"] == "PASS"


def test_live_judge_stays_off_without_a_token_and_hides_a_configured_token(monkeypatch):
    client = _client()
    off = client.get("/ui/live").json()
    assert off["configured"] is False
    assert "Nightshift" in off["message"]
    assert "token" in off["message"].lower()
    secret = "super-secret-token-value"
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", secret)
    on = client.get("/ui/live").json()
    assert on["configured"] is True
    assert secret not in json.dumps(on)
    page = client.get("/ui").text
    assert secret not in page
    monkeypatch.setenv("METERLESS_RUNTIME_TOKEN", "your-nightshift-runtime-token")
    placeholder = client.get("/ui/live").json()
    assert placeholder["configured"] is False


def test_unknown_run_is_404():
    response = _client().get("/ui/runs/does-not-exist")
    assert response.status_code == 404
