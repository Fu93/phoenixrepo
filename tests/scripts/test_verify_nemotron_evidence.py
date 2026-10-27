"""Offline Nemotron evidence check: fail closed on bad traces, pass a good fixture."""

from __future__ import annotations

from pathlib import Path

from app.trace_log import HackathonTrace
from scripts.verify_nemotron_evidence import audit_trace, used_answers

NEMO = "nvidia/nemotron-3-ultra-550b-a55b"
OTHER = "other/model"


def _trace(tmp_path: Path, events: list[dict]) -> Path:
    run_id = "fixture"
    trace = HackathonTrace(run_id, tmp_path)
    for event in events:
        trace.event(
            agent_name="orchestrator",
            action=event.get("action", "LLM_CALL"),
            status=event.get("status", "ok"),
            output_summary=event.get("output_summary", {}),
            llm=event.get("llm"),
        )
    trace.seal()
    return tmp_path / f"trace-{run_id}.jsonl"


def _ok(served: str, *, provider: str | None = "nightshift", tier: str | None = "fast") -> dict:
    summary: dict = {"requested_model": NEMO, "served_model": served}
    if provider is not None:
        summary["provider"] = provider
    if tier is not None:
        summary["tier"] = tier
    return {
        "status": "ok",
        "output_summary": summary,
        "llm": {"requested_model": NEMO, "served_model": served},
    }


def test_good_fixture_passes(tmp_path: Path):
    path = _trace(
        tmp_path,
        [
            {
                "status": "error",
                "output_summary": {},
                "llm": {"requested_model": NEMO, "served_model": OTHER},
            },
            _ok(NEMO),
        ],
    )
    assert audit_trace(path) == []


def test_non_nemotron_served_id_fails(tmp_path: Path):
    path = _trace(tmp_path, [_ok(NEMO), _ok(OTHER, provider="nvidia-direct")])
    problems = audit_trace(path)
    assert problems
    assert any(OTHER in problem and "nvidia/nemotron-" in problem for problem in problems)


def test_missing_provider_is_not_recorded_and_fails(tmp_path: Path):
    path = _trace(tmp_path, [_ok(NEMO, provider=None)])
    rows = used_answers(path)
    assert len(rows) == 1
    assert rows[0].provider is None
    assert rows[0].provider_at == "absent"
    problems = audit_trace(path)
    assert any("provider" in problem and "not recorded" in problem for problem in problems)
    assert all("nightshift" not in problem for problem in problems)


def test_tampered_hash_chain_fails(tmp_path: Path):
    path = _trace(tmp_path, [_ok(NEMO)])
    text = path.read_text(encoding="utf-8")
    assert "nightshift" in text
    path.write_text(text.replace("nightshift", "nightshifX", 1), encoding="utf-8")
    problems = audit_trace(path)
    assert any("hash chain" in problem and "line_hash" in problem for problem in problems)


def test_missing_tier_stays_absent(tmp_path: Path):
    path = _trace(tmp_path, [_ok(NEMO, tier=None)])
    assert audit_trace(path) == []
    rows = used_answers(path)
    assert rows[0].tier is None
    assert rows[0].tier_at == "absent"
