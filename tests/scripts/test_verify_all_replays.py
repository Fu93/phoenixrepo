"""verify_all_replays checks RECORDED catalog entries with the existing trace verifier."""

from __future__ import annotations

import json
from pathlib import Path

from app.trace_log import HackathonTrace
from scripts.verify_all_replays import main

NEMO = "nvidia/nemotron-3-ultra-550b-a55b"


def _catalog(tmp_path: Path, replays: list[dict]) -> Path:
    root = tmp_path / "replays"
    root.mkdir()
    manifest = root / "manifest.json"
    manifest.write_text(json.dumps({"replays": replays}), encoding="utf-8")
    return manifest


def _recorded(root: Path, replay_id: str, *, served: str, llm_used: bool | None, run_hash: str | None = None) -> str:
    directory = root / replay_id
    directory.mkdir()
    trace = HackathonTrace(replay_id, directory)
    # HackathonTrace writes trace-<run_id>.jsonl. The catalog name is trace.jsonl.
    trace.event(
        agent_name="orchestrator",
        action="LLM_CALL",
        output_summary={"served_model": served, "provider": "nightshift"},
        llm={"requested_model": NEMO, "served_model": served},
    )
    sealed = trace.seal()
    written = directory / f"trace-{replay_id}.jsonl"
    written.replace(directory / "trace.jsonl")
    recorded: dict = {"run_hash": run_hash if run_hash is not None else sealed}
    if llm_used is not None:
        recorded["llm_used"] = llm_used
    record = {"verdict": {"verdict": "CANDIDATE"}, "recorded_run": recorded}
    (directory / "record.json").write_text(json.dumps(record), encoding="utf-8")
    return sealed


def test_recorded_replay_passes_and_sample_is_skipped(tmp_path: Path, capsys):
    manifest = _catalog(tmp_path, [])
    root = manifest.parent
    _recorded(root, "good", served=NEMO, llm_used=True)
    # A SAMPLE entry is not checked, even if its directory is absent.
    replays = [
        {"id": "good", "trace_class": "RECORDED"},
        {"id": "sample-only", "trace_class": "SAMPLE"},
    ]
    manifest.write_text(json.dumps({"replays": replays}), encoding="utf-8")
    assert main(["--manifest", str(manifest)]) == 0
    out = capsys.readouterr().out
    assert "good" in out
    assert "sample-only" not in out
    assert "PASS" in out
    assert NEMO in out
    assert "1 passed, 0 failed, 1 RECORDED" in out


def test_wrong_run_hash_fails(tmp_path: Path, capsys):
    manifest = _catalog(tmp_path, [{"id": "bad-hash", "trace_class": "RECORDED"}])
    _recorded(manifest.parent, "bad-hash", served=NEMO, llm_used=True, run_hash="0" * 64)
    assert main(["--manifest", str(manifest)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_used_non_nemotron_answer_fails_when_llm_was_used(tmp_path: Path, capsys):
    manifest = _catalog(tmp_path, [{"id": "gemini", "trace_class": "RECORDED"}])
    _recorded(manifest.parent, "gemini", served="gemini-3.1-flash-lite", llm_used=True)
    assert main(["--manifest", str(manifest)]) == 1
    assert "not nemotron" in capsys.readouterr().out


def test_repo_recorded_replays_pass():
    assert main([]) == 0
