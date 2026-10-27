"""The E3 experiment replays: labelled as not the scored run, chain and Nemotron checks pass."""
from __future__ import annotations

import json
from pathlib import Path

from app.replay_ui import replay_payload
from app.trace_log import HackathonTrace
from scripts.import_replay import import_replay
from scripts.verify_trace import nemotron_report, verify

ROOT = Path(__file__).resolve().parents[2]
REPLAYS = ROOT / "data" / "replays"
TITLE = "Experiment E3: NVIDIA direct, thinking on, temperature 1.0, not the scored run"
E3 = {
    "e3-goodreasonai-scrapeserv": ("judge-20261027042854", "ad556177edc4bb186251ecbb5a5dde256a255e4fc4742935677716bb2401480b"),
    "e3-dsregoitia-calendarsyncproject": ("judge-20261027041913", "1fbf3ae09de5c72eaa02c866aa9e867d025b579e42b4038f72beda94681f3052"),
}


def test_import_replay_accepts_title_banner_and_summary(tmp_path):
    trace = HackathonTrace("live-2", log_dir=tmp_path)
    trace.event(
        agent_name="orchestrator",
        action="LLM_CALL",
        input_summary={"repo_id": "acme/widget", "step": "discovery"},
        llm={"requested_model": "nvidia/nemotron-3-ultra-550b-a55b", "served_model": "nvidia/nemotron-3-ultra-550b-a55b"},
        status="ok",
    )
    trace.seal()
    scores = {"run_id": "live-2", "run_hash": trace.run_hash, "llm_used": True,
              "ineligible": [{"repo_id": "acme/widget", "verdict": "ALREADY_EXISTS", "reason": "x"}]}
    verdict = tmp_path / "scores.json"
    verdict.write_text(json.dumps(scores), encoding="utf-8")
    replays = tmp_path / "replays"
    replays.mkdir()
    entry = import_replay(trace.path, verdict, replays_root=replays, replay_id="e3-x", title=TITLE, banner="B", summary="S")
    record = json.loads((replays / "e3-x" / "record.json").read_text(encoding="utf-8"))
    assert entry["title"] == record["title"] == TITLE
    assert record["banner"] == "B" and record["summary"] == "S"
    assert (replays / "e3-x" / "trace.jsonl").read_bytes() == trace.path.read_bytes()
    assert b"\r\n" not in (replays / "manifest.json").read_bytes()


def test_committed_e3_replays_are_labelled_and_verify():
    manifest = {row["id"]: row for row in json.loads((REPLAYS / "manifest.json").read_text(encoding="utf-8"))["replays"]}
    for replay_id, (run_id, run_hash) in E3.items():
        assert manifest[replay_id]["title"] == TITLE
        assert manifest[replay_id]["trace_class"] == "RECORDED"
        trace = REPLAYS / replay_id / "trace.jsonl"
        ok, detail = verify(trace, expected_run_hash=run_hash)
        assert ok, detail
        report = nemotron_report(trace)
        assert report["answers_used"] > 0 and not report["not_nemotron"]
        assert set(report["providers"]) == {"nvidia-direct"}
        payload = replay_payload(replay_id)
        assert payload["recorded_run"]["run_id"] == run_id
        assert "not the scored" in payload["banner"].lower()
        thought = payload["verdict_reasoning"]
        assert thought and thought["text"] and thought["nemotron"] is True and thought["provider"] == "nvidia-direct"
