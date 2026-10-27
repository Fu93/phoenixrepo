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


def test_a_bad_row_or_a_broken_trace_does_not_change_the_verdict(tmp_path):
    row = {"repo_id": "o/r", "verdict": "CANDIDATE", "reason": "café — 中文"}
    trace = HackathonTrace("run-1", log_dir=tmp_path)
    out = remember(["not-a-row", row], trace=trace, log_dir=tmp_path, run_id="run-1")
    assert out[0]["verdict"] == "CANDIDATE" and row["verdict"] == "CANDIDATE"
    store = json.loads((tmp_path / "hmem" / "gate1_verdicts.json").read_bytes().decode("utf-8"))
    assert "café" in store["records"][0]["content"] and "中文" in store["records"][0]["content"]

    class BoomTrace(HackathonTrace):
        def event(self, **kwargs):
            raise RuntimeError("trace down")

    again = {"repo_id": "o/r", "verdict": "NO_GO", "reason": "why"}
    broken = BoomTrace("run-2", log_dir=tmp_path)
    assert remember([again], trace=broken, log_dir=tmp_path, run_id="run-2") == []
    assert again["verdict"] == "NO_GO"


def test_served_model_comes_from_the_row_or_the_reason(tmp_path):
    trace = HackathonTrace("run-1", log_dir=tmp_path)
    reason = "Method: llm+verified quote (requested=nvidia/nemotron-3-super-120b-a12b, served=nvidia/nemotron-3-ultra-550b-a55b)."
    remember(
        [{"repo_id": "o/r", "verdict": "CANDIDATE", "reason": reason}],
        trace=trace, log_dir=tmp_path, run_id="run-1",
    )
    store = json.loads((tmp_path / "hmem" / "gate1_verdicts.json").read_text(encoding="utf-8"))
    assert store["records"][0]["extra"]["served_model"] == "nvidia/nemotron-3-ultra-550b-a55b"

    trace2 = HackathonTrace("run-2", log_dir=tmp_path)
    remember(
        [{"repo_id": "Owner/Repo", "verdict": "NO_GO", "reason": "plain", "served_model": "nvidia/explicit"}],
        trace=trace2, log_dir=tmp_path, run_id="run-2",
        served_by_repo={"owner/repo": "nvidia/from-map"},
    )
    store = json.loads((tmp_path / "hmem" / "gate1_verdicts.json").read_text(encoding="utf-8"))
    explicit = next(r for r in store["records"] if r["source"] == "run-2")
    assert explicit["extra"]["served_model"] == "nvidia/explicit"


def test_repo_id_match_is_case_insensitive(tmp_path):
    _run(tmp_path, "run-1", "CANDIDATE")
    # _run writes owner path "o/r". A different case must supersede that same repository.
    trace = HackathonTrace("run-2", log_dir=tmp_path)
    out = remember(
        [{"repo_id": "O/R", "verdict": "NO_GO", "reason": "why"}],
        trace=trace, log_dir=tmp_path, run_id="run-2",
    )
    assert out[0]["verdict_changed"] is True
    store = json.loads((tmp_path / "hmem" / "gate1_verdicts.json").read_text(encoding="utf-8"))
    live = [r for r in store["records"] if not r.get("superseded_by")]
    assert len(live) == 1 and live[0]["extra"]["verdict"] == "NO_GO"


def test_a_failed_supersede_does_not_leave_two_live_verdicts(tmp_path, monkeypatch):
    _run(tmp_path, "run-1", "CANDIDATE")
    import app.engines.hmem.store as store_mod

    def boom(self, old_id, new_id):
        raise RuntimeError("supersede failed")

    monkeypatch.setattr(store_mod.MemoryStoreService, "supersede", boom)
    trace, out = _run(tmp_path, "run-2", "NO_GO")
    assert out[0]["verdict"] == "NO_GO"
    store = json.loads((tmp_path / "hmem" / "gate1_verdicts.json").read_text(encoding="utf-8"))
    live = [r for r in store["records"] if not r.get("superseded_by")]
    assert len(live) == 1


def test_a_corrupt_store_is_renamed_and_a_torn_ledger_line_is_skipped(tmp_path):
    directory = tmp_path / "hmem"
    directory.mkdir()
    (directory / "gate1_verdicts.json").write_text("{not json", encoding="utf-8")
    (directory / "gate1_ledger.jsonl").write_text('{"torn"\n', encoding="utf-8")
    trace, out = _run(tmp_path, "run-1", "CANDIDATE")
    assert out[0]["verdict"] == "CANDIDATE"
    assert (directory / "gate1_verdicts.json.bak").read_text(encoding="utf-8").startswith("{not")
    store = json.loads((directory / "gate1_verdicts.json").read_text(encoding="utf-8"))
    assert len(store["records"]) == 1
    assert _events(trace)[0]["status"] == "ok"
