"""import_replay copies a bundled example trace into a replay catalog the UI can read."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from api.run import app

ROOT = Path(__file__).resolve().parents[2]
TRACE = ROOT / "data" / "replays" / "invoice-already-exists" / "trace.jsonl"
VERDICT = ROOT / "data" / "replays" / "invoice-already-exists" / "record.json"
CLIENT = TestClient(app)


def test_import_replay_cli_on_the_bundled_invoice_trace(tmp_path, monkeypatch):
    replays = tmp_path / "replays"
    replays.mkdir()
    (replays / "manifest.json").write_text(json.dumps({"replays": [], "missing": []}), encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            "scripts/import_replay.py",
            str(TRACE),
            "--verdict",
            str(VERDICT),
            "--replays-dir",
            str(replays),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    replay_id = proc.stdout.strip()
    assert replay_id == "tharani-2006-invoice-generator-application"
    copied = (replays / replay_id / "trace.jsonl").read_bytes()
    assert copied == TRACE.read_bytes()
    monkeypatch.setattr("app.replay_ui.REPLAYS_ROOT", replays)
    body = CLIENT.get(f"/api/replays/{replay_id}").json()
    assert body["trace_class"] == "RECORDED"
    assert body["verdict"]["verdict"] == "ALREADY_EXISTS"
    assert "verdict_kind" not in body["title"]
    assert "Invoify is a free" in body["verdict"]["quote"]
    assert body["verdict"]["gate2"]["why"].startswith("No revive trace")
    assert body["verification"]["ok"] is True
    assert body["verdict"]["rescue"]["score"] is not None
    assert "not a SAMPLE seal" not in body["banner"]
