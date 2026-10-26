"""verify_trace --require-nemotron: every used answer must be served by nvidia/nemotron-*."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.trace_log import HackathonTrace
from scripts import verify_trace

NEMO = "nvidia/nemotron-3-ultra-550b-a55b"


def _trace(tmp_path: Path, calls: list[tuple[str, str, str]]) -> Path:
    trace = HackathonTrace("nemo-check", log_dir=tmp_path)
    for served, status, provider in calls:
        summary = {"requested_model": NEMO, "served_model": served}
        if provider != "nightshift":
            summary["provider"] = provider
        trace.event(agent_name="orchestrator", action="LLM_CALL", input_summary={"repo_id": "o/r", "step": "problem"},
                    output_summary=summary, status=status, llm={"requested_model": NEMO, "served_model": served})
    trace.seal()
    return trace.path


def test_all_nemotron_passes_and_prints_the_provider_split(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    path = _trace(tmp_path, [(NEMO, "ok", "nightshift"), (NEMO, "ok", "nvidia-direct"), ("gemini-flash", "rejected", "nightshift")])
    assert verify_trace.main([str(path), "--require-nemotron"]) == 0
    out = capsys.readouterr().out
    assert "nemotron: 2 of 2 answers used" in out
    assert "nightshift 1" in out and "nvidia-direct 1" in out
    assert "discarded by policy: 1" in out


def test_a_used_non_nemotron_answer_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    path = _trace(tmp_path, [(NEMO, "ok", "nightshift"), ("gemini-flash", "ok", "nightshift")])
    assert verify_trace.main([str(path), "--require-nemotron"]) == 1
    assert "not nemotron: line" in capsys.readouterr().out
    # Without the flag the chain alone still verifies.
    assert verify_trace.main([str(path)]) == 0


def test_editing_served_model_to_nemotron_breaks_the_chain(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    path = _trace(tmp_path, [("gemini-flash", "ok", "nightshift")])
    lines = path.read_text(encoding="utf-8").splitlines()
    record = json.loads(lines[0])
    record["served_model"] = NEMO
    lines[0] = json.dumps(record)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert verify_trace.main([str(path), "--require-nemotron"]) == 1
    assert "line_hash does not match" in capsys.readouterr().out


def test_a_trace_with_no_model_answer_fails(tmp_path: Path):
    path = _trace(tmp_path, [])
    assert verify_trace.main([str(path), "--require-nemotron"]) == 1
