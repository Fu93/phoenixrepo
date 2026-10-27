"""Gate-1 verdict memory on H-MEM: records, flags a change, never decides."""
from __future__ import annotations

import json

from app.trace_log import HackathonTrace
from app.verdict_memory import remember


def _events(trace: HackathonTrace) -> list[dict]:
    lines = [json.loads(x) for x in trace.path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return [x for x in lines if x.get("action") == "HMEM_VERDICT_MEMORY"]


def _run(tmp_path, run_id, verdict):
    trace = HackathonTrace(run_id, log_dir=tmp_path)
    out = remember([{"repo_id": "o/r", "verdict": verdict, "reason": "why"}], trace=trace, log_dir=tmp_path, run_id=run_id)
    trace.seal()
    return trace, out


def test_first_verdict_is_remembered(tmp_path):
    trace, out = _run(tmp_path, "run-1", "CANDIDATE")
    assert out[0]["remembered_before"] == [] and out[0]["verdict_changed"] is False
    store = json.loads((tmp_path / "hmem" / "gate1_verdicts.json").read_text(encoding="utf-8"))
    rec = store["records"][0]
    assert rec["layer"] == "long_term" and rec["extra"]["verdict"] == "CANDIDATE" and "o/r" in rec["entities"]
    assert json.loads(_events(trace)[0]["output_summary"])["memory_id"] == rec["id"]


def test_same_verdict_again_is_not_flagged(tmp_path):
    _run(tmp_path, "run-1", "NO_GO")
    _, out = _run(tmp_path, "run-2", "NO_GO")
    assert out[0]["verdict_changed"] is False
    assert out[0]["remembered_before"][0] == {"verdict": "NO_GO", "run_id": "run-1"}


def test_changed_verdict_is_flagged_and_old_one_kept(tmp_path):
    _run(tmp_path, "run-1", "CANDIDATE")
    trace, out = _run(tmp_path, "run-2", "NEEDS_PRIOR_ART")
    assert out[0]["verdict"] == "NEEDS_PRIOR_ART"  # the run's verdict, unchanged
    assert out[0]["verdict_changed"] is True
    store = json.loads((tmp_path / "hmem" / "gate1_verdicts.json").read_text(encoding="utf-8"))
    assert len(store["records"]) == 2
    old = next(r for r in store["records"] if r["source"] == "run-1")
    assert old["superseded_by"] == out[0]["memory_id"]
    ledger = (tmp_path / "hmem" / "gate1_ledger.jsonl").read_text(encoding="utf-8")
    assert "conflict_detected" in ledger
    # a third run compares with the newest remembered verdict, not the superseded one
    _, out3 = _run(tmp_path, "run-3", "NEEDS_PRIOR_ART")
    assert out3[0]["verdict_changed"] is False and len(out3[0]["remembered_before"]) == 1


def test_off_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOENIX_HMEM", "off")
    trace, out = _run(tmp_path, "run-1", "CANDIDATE")
    assert out == [] and _events(trace) == [] and not (tmp_path / "hmem").exists()


def test_hmem_error_never_stops_gate1(tmp_path, monkeypatch):
    import app.verdict_memory as vm

    def boom(_):
        raise OSError("disk full")

    monkeypatch.setattr(vm, "_store", boom)
    trace, out = _run(tmp_path, "run-1", "CANDIDATE")
    assert out == [] and _events(trace)[0]["status"] == "error"
