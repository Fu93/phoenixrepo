import json

from app.trace_log import HackathonTrace
from scripts.verify_trace import verify


def test_verify_trace_accepts_a_sealed_chain(tmp_path):
    trace = HackathonTrace("run-1", tmp_path)
    trace.event(agent_name="orchestrator", action="RUN_STARTED", output_summary="ok")
    trace.seal()
    ok, detail = verify(trace.path)
    assert ok is True
    assert detail == trace.run_hash


def test_verify_trace_rejects_a_tampered_line(tmp_path):
    trace = HackathonTrace("run-2", tmp_path)
    trace.event(agent_name="orchestrator", action="RUN_STARTED", output_summary="intact")
    trace.seal()
    lines = trace.path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["output_summary"] = "tampered"
    lines[0] = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    trace.path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ok, detail = verify(trace.path)
    assert ok is False
    assert "line_hash" in detail
