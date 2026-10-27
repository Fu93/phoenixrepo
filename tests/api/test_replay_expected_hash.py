"""Replay verification uses recorded_run.run_hash when record.json has one."""

from __future__ import annotations

import json
from pathlib import Path

from app.replay_ui import flip_one_character, replay_payload
from app.trace_log import HackathonTrace
from scripts.verify_trace import verify


def _write_replay(
    root: Path,
    replay_id: str,
    trace_text: str,
    run_hash: str | None,
    *,
    record_class: str = "RECORDED",
    manifest_class: str | None = None,
) -> None:
    directory = root / replay_id
    directory.mkdir(parents=True)
    (directory / "trace.jsonl").write_text(trace_text, encoding="utf-8")
    recorded = None
    if run_hash is not None:
        recorded = {
            "run_id": "run-1",
            "run_hash": run_hash,
            "source": "test",
            "jsonl_committed": True,
        }
    record = {
        "id": replay_id,
        "title": "t",
        "repo_id": "acme/widget",
        "data_class": "RECORDED",
        "trace_class": record_class,
        "banner": "b",
        "verdict": {"verdict": "CANDIDATE"},
        "recorded_run": recorded,
    }
    (directory / "record.json").write_text(json.dumps(record), encoding="utf-8")
    manifest_path = root / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"replays": []}
    listed = record_class if manifest_class is None else manifest_class
    rows = [row for row in manifest.get("replays") or [] if row.get("id") != replay_id]
    rows.append({"id": replay_id, "title": "t", "trace_class": listed})
    manifest["replays"] = rows
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _seal(tmp_path: Path, run_id: str, text: str) -> HackathonTrace:
    trace = HackathonTrace(run_id, log_dir=tmp_path)
    trace.event(agent_name="orchestrator", action="NOTE", output_summary=text, status="ok")
    trace.seal()
    return trace


def test_matching_recorded_hash_passes(tmp_path: Path, monkeypatch):
    trace = _seal(tmp_path, "match-1", "original")
    root = tmp_path / "replays"
    _write_replay(root, "match-1", trace.path.read_text(encoding="utf-8"), trace.run_hash)
    monkeypatch.setattr("app.replay_ui.REPLAYS_ROOT", root)
    body = replay_payload("match-1")
    assert body["verification"]["ok"] is True
    assert body["verification"]["state"] == "pass"
    assert body["verification"]["detail"] == trace.run_hash
    assert body["verification"]["expected_run_hash"] == trace.run_hash


def test_resealed_trace_with_the_wrong_expected_hash_fails(tmp_path: Path, monkeypatch):
    original = _seal(tmp_path, "orig", "first")
    resealed = _seal(tmp_path, "reseal", "rewritten")
    assert resealed.run_hash != original.run_hash
    chain_ok, chain_detail = verify(resealed.path)
    assert chain_ok is True and chain_detail == resealed.run_hash
    root = tmp_path / "replays"
    _write_replay(root, "reseal", resealed.path.read_text(encoding="utf-8"), original.run_hash)
    monkeypatch.setattr("app.replay_ui.REPLAYS_ROOT", root)
    body = replay_payload("reseal")
    assert body["verification"]["ok"] is False
    assert body["verification"]["state"] == "fail"
    assert "does not match the expected run hash" in body["verification"]["detail"]
    assert resealed.run_hash in body["verification"]["detail"]


def test_tampered_trace_with_the_expected_hash_fails(tmp_path: Path, monkeypatch):
    trace = _seal(tmp_path, "tamper-1", "lettered")
    flipped = flip_one_character(trace.path.read_text(encoding="utf-8"))
    root = tmp_path / "replays"
    _write_replay(root, "tamper-1", flipped, trace.run_hash)
    monkeypatch.setattr("app.replay_ui.REPLAYS_ROOT", root)
    body = replay_payload("tamper-1")
    assert body["verification"]["ok"] is False
    assert body["verification"]["state"] == "fail"
    assert body["verification"]["expected_run_hash"] == trace.run_hash


def test_missing_recorded_hash_is_an_explicit_state(tmp_path: Path, monkeypatch):
    trace = _seal(tmp_path, "no-hash", "sealed")
    root = tmp_path / "replays"
    _write_replay(root, "no-hash", trace.path.read_text(encoding="utf-8"), None)
    monkeypatch.setattr("app.replay_ui.REPLAYS_ROOT", root)
    body = replay_payload("no-hash")
    assert body["verification"]["ok"] is False
    assert body["verification"]["state"] == "no_recorded_hash"
    assert body["verification"]["detail"] == "no recorded hash to compare"

    record_path = root / "no-hash" / "record.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["recorded_run"] = {"run_id": "run-1", "run_hash": "  "}
    record_path.write_text(json.dumps(record), encoding="utf-8")
    blank = replay_payload("no-hash")
    assert blank["verification"]["state"] == "no_recorded_hash"
    assert blank["verification"]["detail"] == "no recorded hash to compare"


def test_sample_manifest_is_neutral_when_the_recorded_hash_differs(tmp_path: Path, monkeypatch):
    original = _seal(tmp_path, "sample-orig", "first")
    sealed = _seal(tmp_path, "sample-seal", "different seal")
    assert sealed.run_hash != original.run_hash
    root = tmp_path / "replays"
    _write_replay(
        root,
        "sample-1",
        sealed.path.read_text(encoding="utf-8"),
        original.run_hash,
        record_class="RECORDED",
        manifest_class="SAMPLE",
    )
    monkeypatch.setattr("app.replay_ui.REPLAYS_ROOT", root)
    body = replay_payload("sample-1")
    check = body["verification"]
    assert check["state"] == "sample"
    assert check["state"] != "fail"
    assert check["ok"] is False
    assert check["compared"] is False
    assert check["detail"] == "sample, not a recorded run: hash not compared"
    assert "expected_run_hash" not in check
    assert "reconstruction sealed over committed files" in check["note"]


def test_recorded_mismatch_still_fails(tmp_path: Path, monkeypatch):
    original = _seal(tmp_path, "rec-orig", "first")
    resealed = _seal(tmp_path, "rec-reseal", "rewritten")
    root = tmp_path / "replays"
    _write_replay(
        root,
        "recorded-1",
        resealed.path.read_text(encoding="utf-8"),
        original.run_hash,
        manifest_class="RECORDED",
    )
    monkeypatch.setattr("app.replay_ui.REPLAYS_ROOT", root)
    body = replay_payload("recorded-1")
    assert body["verification"]["state"] == "fail"
    assert body["verification"]["ok"] is False
    assert "does not match the expected run hash" in body["verification"]["detail"]


def test_tampered_recorded_trace_fails(tmp_path: Path, monkeypatch):
    trace = _seal(tmp_path, "rec-tamper", "lettered")
    flipped = flip_one_character(trace.path.read_text(encoding="utf-8"))
    root = tmp_path / "replays"
    _write_replay(root, "recorded-tamper", flipped, trace.run_hash, manifest_class="RECORDED")
    monkeypatch.setattr("app.replay_ui.REPLAYS_ROOT", root)
    body = replay_payload("recorded-tamper")
    assert body["verification"]["state"] == "fail"
    assert body["verification"]["ok"] is False
    assert body["verification"]["expected_run_hash"] == trace.run_hash


def test_committed_replays_use_the_record_json_hash():
    matched = replay_payload("selfdb-io-selfdb")
    assert matched["trace_class"] == "RECORDED"
    assert matched["verification"]["ok"] is True
    assert matched["verification"]["state"] == "pass"
    assert matched["verification"]["detail"] == matched["recorded_run"]["run_hash"]

    sample = replay_payload("invoice-already-exists")
    assert sample["trace_class"] == "SAMPLE"
    assert sample["verification"]["state"] == "sample"
    assert sample["verification"]["ok"] is False
    assert sample["verification"]["detail"] == "sample, not a recorded run: hash not compared"
    gate2 = replay_payload("invoice-gate2-sample")
    assert gate2["verification"]["state"] == "sample"
    assert gate2["verification"]["detail"] == "sample, not a recorded run: hash not compared"

    page = Path(__file__).resolve().parents[2].joinpath("app/ui/index.html").read_text(encoding="utf-8")
    assert 'check.state === "sample"' in page
    assert 'status.className = "muted"' in page
    assert "sample, not a recorded run: hash not compared" in page
