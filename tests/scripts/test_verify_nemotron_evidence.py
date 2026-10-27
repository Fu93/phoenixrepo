"""Offline Nemotron evidence check: fail closed on bad traces, pass a good fixture."""

from __future__ import annotations

from pathlib import Path

from app.trace_log import HackathonTrace
from scripts.verify_nemotron_evidence import INFERRED_PROVIDER_LABEL, audit_trace, provider_section, used_answers

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


def test_explicit_nvidia_direct_is_recorded(tmp_path: Path):
    path = _trace(tmp_path, [_ok(NEMO, provider="nvidia-direct")])
    rows = used_answers(path)
    assert rows[0].provider == "nvidia-direct"
    assert rows[0].provider_at == "output_summary"
    assert audit_trace(path) == []
    failures: list[str] = []
    text = "\n".join(provider_section(rows, expected_total=1, expected_direct=1, failures=failures))
    assert failures == []
    assert "provider nvidia-direct (explicitly recorded): recomputed 1, expected 1, PASS" in text
    assert f"{INFERRED_PROVIDER_LABEL}: derived 0 (1 total - 1 explicit nvidia-direct = 0), expected 0, PASS" in text
    assert "provider nightshift explicitly recorded: 0" in text
    assert "nightshift: 168" not in text
    assert "provider nightshift: recomputed" not in text


def test_missing_provider_is_inferred_never_recorded(tmp_path: Path):
    path = _trace(tmp_path, [_ok(NEMO, provider=None), _ok(NEMO, provider="nvidia-direct")])
    rows = used_answers(path)
    assert [row.provider for row in rows] == [None, "nvidia-direct"]
    assert rows[0].provider_at == "absent"
    assert rows[0].provider != "nightshift"
    assert audit_trace(path) == []
    failures: list[str] = []
    text = "\n".join(provider_section(rows, expected_total=2, expected_direct=1, failures=failures))
    assert failures == []
    assert "provider nvidia-direct (explicitly recorded): recomputed 1, expected 1, PASS" in text
    assert INFERRED_PROVIDER_LABEL in text
    assert "derived 1 (2 total - 1 explicit nvidia-direct = 1), expected 1, PASS" in text
    assert "This is not an explicitly recorded field." in text
    assert "provider nightshift explicitly recorded: 0" in text
    assert "nightshift: 1" not in text
    assert "nightshift: 168" not in text
    assert "provider nightshift: recomputed" not in text
    assert "not recorded" not in text


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
