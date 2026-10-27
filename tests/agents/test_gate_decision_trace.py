"""GATE_DECISION records how the blocker gate treated the model's raw verdict.

The event is on the same hash chain as DISCOVERY_FILTER. Turning it off does not
change the verdict or the reason. Traces sealed before the event existed still verify.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

from app.agents.relevance import RelevanceClient, judge_discovery
from app.trace_log import HackathonTrace, line_hash
from scripts.verify_trace import verify

ROOT = Path(__file__).resolve().parents[2]
PAGE = "PAGE_TEXT_DO_NOT_RECORD_" + ("q" * 240)
README_PAGE = "README_PAGE_TEXT_DO_NOT_RECORD"
REASON_LEAK = "REASON_TEXT_DO_NOT_RECORD"
TOKEN = "nvapi-" + ("z" * 30)
RECENT = (date.today() - timedelta(days=10)).isoformat() + "T00:00:00Z"

CANDIDATE_OWN = {
    "repo_id": "phillipdupuis/pydantic-to-typescript",
    "html_url": "https://github.com/phillipdupuis/pydantic-to-typescript",
    "description": "Generate TypeScript interfaces from pydantic models.",
    "readme_excerpt": "Generate TypeScript interfaces from pydantic models.",
    "manifests": {
        "pyproject.toml": '[project]\nname = "pydantic-to-typescript"\n',
    },
}


def _client(tmp_path: Path, payload: dict, trace: HackathonTrace | None) -> RelevanceClient:
    return RelevanceClient(
        cache_dir=tmp_path,
        mock_handler=lambda _prompt, _sys: json.dumps(payload),
        trace=trace,
    )


def _judge(tmp_path: Path, spec: dict, trace: HackathonTrace | None):
    judged = judge_discovery(
        spec["candidate"],
        spec.get("priors") or [],
        [],
        client=_client(tmp_path, spec["llm"], trace),
        problem_alternatives=spec.get("alternatives") or [],
    )
    return judged


def _gate_events(trace: HackathonTrace) -> list[dict]:
    events = []
    for line in trace.path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("action") == "GATE_DECISION":
            events.append(record)
    return events


def _payload(record: dict) -> dict:
    summary = record["output_summary"]
    if isinstance(summary, str):
        summary = json.loads(summary)
    assert isinstance(summary, dict)
    return summary


def _p1() -> dict:
    return {
        "name": "P1",
        "candidate": {
            "repo_id": "acme/notes",
            "description": "A small tool that formats notes.",
            "readme_excerpt": f"A small tool that formats notes.\n{README_PAGE}",
            "stars": 12,
        },
        "llm": {
            "verdict": "CANDIDATE",
            "reason": f"{REASON_LEAK} the notes tool looks new",
            "problem_quote": "A small tool that formats notes.",
        },
        "path": "P1",
        "final": "CANDIDATE",
        "raw": "CANDIDATE",
        "outcome": "not_applied",
        "applied": False,
        "shortcut": None,
        "blocks": False,
    }


def _p3() -> dict:
    return {
        "name": "P3",
        "candidate": {
            "repo_id": "samwho/spacer",
            "description": "Add spacers between the characters of a file.",
            "readme_excerpt": "Add spacers between the characters of a file.",
            "stars": 1818,
        },
        "priors": [{
            "alternative_id": "hionay/tailer",
            "url": "https://github.com/hionay/tailer",
            "title": "tailer",
            "excerpt": "tailer is a Go port of spacer.",
            "description": PAGE,
            "readme": TOKEN,
            "overlap": "full",
            "same_problem": True,
            "same_job": True,
            "stars": 10,
            "pushed_at": "2020-01-01T00:00:00Z",
            "archived": False,
            "licence_spdx": "MIT",
            "page_kind": "product",
        }],
        "llm": {
            "verdict": "ALREADY_EXISTS",
            "reason": f"{REASON_LEAK} tailer already does this",
            "problem_quote": "Add spacers between the characters of a file.",
            "alternative_repo": "hionay/tailer",
            "alternative_quote": "a Go port of spacer",
        },
        "path": "P3",
        "final": "CANDIDATE",
        "raw": "ALREADY_EXISTS",
        "outcome": "failed",
        "applied": True,
        "shortcut": None,
        "blocks": False,
        "checks": {"A": "pass", "B": "fail", "C": "fail", "D": "pass", "E": "pass"},
        "named_id": "hionay/tailer",
    }


def _bf() -> dict:
    return {
        "name": "BF",
        "candidate": {
            "repo_id": "acme/widget",
            "description": "Sync notes to a folder.",
            "readme_excerpt": "Sync notes to a folder on disk.",
            "stars": 20,
        },
        "alternatives": [{
            "alternative_id": "someone/tool",
            "url": "https://github.com/someone/tool",
            "title": "Someone Tool",
            "same_problem": True,
            "overlap": "full",
            "same_job": True,
            "quotes": ["sync notes to a folder"],
            "excerpt": "sync notes to a folder",
            "description": PAGE,
            "text": TOKEN,
            "stars": 5000,
            "pushed_at": RECENT,
            "archived": False,
            "licence_spdx": "MIT",
            "page_kind": "product",
        }],
        "llm": {
            "verdict": "CANDIDATE",
            "reason": f"{REASON_LEAK} nothing else syncs notes",
            "problem_quote": "Sync notes to a folder",
            "alternative_repo": "other/unrelated",
        },
        "path": "BF",
        "final": "ALREADY_EXISTS",
        "raw": "CANDIDATE",
        "outcome": "passed",
        "applied": True,
        "shortcut": None,
        "blocks": True,
        "checks": {"A": "pass", "B": "pass", "C": "pass", "D": "pass", "E": "pass"},
        "named_id": "other/unrelated",
        "gate_id": "someone/tool",
    }


def _own() -> dict:
    return {
        "name": "own-identity",
        "candidate": CANDIDATE_OWN,
        "priors": [{
            "alternative_id": "web:https://pypi.org/project/pydantic-to-typescript/",
            "url": "https://pypi.org/project/pydantic-to-typescript/",
            "title": "pydantic-to-typescript",
            "description": PAGE,
            "excerpt": TOKEN,
            "source": "web",
        }],
        "llm": {
            "verdict": "ALREADY_EXISTS",
            "reason": f"{REASON_LEAK} the PyPI page is this package",
            "problem_quote": "Generate TypeScript interfaces from pydantic models.",
            "alternative_repo": "https://pypi.org/project/pydantic-to-typescript/",
            "alternative_quote": "Generate TypeScript interfaces from pydantic models.",
        },
        "path": "own-identity",
        "final": "CANDIDATE",
        "raw": "ALREADY_EXISTS",
        "outcome": "shortcut",
        "applied": False,
        "shortcut": "own-identity",
        "blocks": False,
        "named_url": "https://pypi.org/project/pydantic-to-typescript/",
    }


def _vendor_docs() -> dict:
    quote = "PagerDuty's Datadog integration sends Datadog alerts into PagerDuty incidents."
    return {
        "name": "gate-pass",
        "candidate": {
            "repo_id": "sergiitk/pagerbeauty",
            "description": "Live PagerDuty on-call schedule viewer.",
            "readme_excerpt": "Shows who is on call from a PagerDuty schedule.",
            "stars": 400,
        },
        "alternatives": [{
            "alternative_id": "web:https://support.pagerduty.com/main/docs/datadog-integration",
            "url": "https://support.pagerduty.com/main/docs/datadog-integration",
            "title": "PagerDuty Datadog integration guide",
            "excerpt": quote,
            "description": PAGE,
            "readme": TOKEN,
            "quotes": [quote],
            "quote_verified": True,
            "same_problem": True,
            "same_job": True,
            "overlap": "full",
            "source": "web",
            "stars": 0,
            "archived": False,
            "page_kind": "product",
        }],
        "llm": {
            "verdict": "ALREADY_EXISTS",
            "reason": "the vendor docs already do this job",
            "problem_quote": "Shows who is on call from a PagerDuty schedule.",
            "alternative_repo": "https://support.pagerduty.com/main/docs/datadog-integration",
            "alternative_quote": quote,
        },
        "path": "gate-pass",
        "final": "ALREADY_EXISTS",
        "raw": "ALREADY_EXISTS",
        "outcome": "passed",
        "applied": True,
        "shortcut": "first_party_vendor_docs",
        "blocks": True,
        "checks": {"A": "pass", "B": "shortcut", "C": "shortcut", "D": "pass", "E": "pass"},
        "gate_id": "web:https://support.pagerduty.com/main/docs/datadog-integration",
    }


FIXTURES = (_p1, _p3, _bf, _own, _vendor_docs)


def _assert_clean(trace: HackathonTrace) -> None:
    text = trace.path.read_text(encoding="utf-8")
    for secret in (PAGE, README_PAGE, REASON_LEAK, TOKEN, "Bearer", "excerpt"):
        assert secret not in text, secret


def _assert_shape(spec: dict, record: dict) -> None:
    body = _payload(record)
    assert set(body) == {
        "repo_id", "raw_verdict", "named_alternative", "gate_alternative",
        "gate", "final_verdict", "path", "coverage_override",
    }
    assert body["repo_id"] == spec["candidate"]["repo_id"]
    assert body["raw_verdict"] == spec["raw"]
    assert body["final_verdict"] == spec["final"]
    assert body["path"] == spec["path"]
    assert body["coverage_override"] is False
    for key in ("named_alternative", "gate_alternative"):
        assert set(body[key]) == {"id", "url", "title"}
    if spec.get("named_id"):
        assert body["named_alternative"]["id"] == spec["named_id"]
    if spec.get("named_url"):
        assert body["named_alternative"]["url"] == spec["named_url"]
    if spec.get("gate_id"):
        assert body["gate_alternative"]["id"] == spec["gate_id"]
    gate = body["gate"]
    assert set(gate) == {
        "applied", "outcome", "checks", "failed", "fail_reasons", "shortcut", "role", "blocks",
    }
    assert gate["applied"] is spec["applied"]
    assert gate["outcome"] == spec["outcome"]
    assert gate["shortcut"] == spec["shortcut"]
    assert gate["blocks"] is spec["blocks"]
    if "checks" in spec:
        assert gate["checks"] == spec["checks"]
        assert gate["failed"] == [letter for letter, state in spec["checks"].items() if state == "fail"]
    else:
        assert gate["checks"] == {}
        assert gate["failed"] == []
        assert gate["fail_reasons"] == {}
    blob = json.dumps(body)
    for secret in (PAGE, README_PAGE, REASON_LEAK, TOKEN):
        assert secret not in blob


def test_event_on_and_off_keep_the_same_verdict_and_reason(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PHOENIX_TRACE_GATE_DECISION", raising=False)
    for build in FIXTURES:
        spec = build()
        on_trace = HackathonTrace(f"on-{spec['name']}", tmp_path / spec["name"] / "on")
        off_trace = HackathonTrace(f"off-{spec['name']}", tmp_path / spec["name"] / "off")
        bare = _judge(tmp_path / spec["name"] / "bare", spec, None)
        on = _judge(tmp_path / spec["name"] / "on-cache", spec, on_trace)
        monkeypatch.setenv("PHOENIX_TRACE_GATE_DECISION", "0")
        off = _judge(tmp_path / spec["name"] / "off-cache", spec, off_trace)
        monkeypatch.delenv("PHOENIX_TRACE_GATE_DECISION", raising=False)

        assert on[0] == off[0] == bare[0] == spec["final"]
        assert on[1] == off[1] == bare[1]
        assert on.evidence == off.evidence == bare.evidence
        assert on.must_beat == off.must_beat == bare.must_beat

        found = _gate_events(on_trace)
        assert len(found) == 1
        _assert_shape(spec, found[0])
        _assert_clean(on_trace)
        assert _gate_events(off_trace) == []
        on_trace.seal()
        off_trace.seal()
        assert verify(on_trace.path)[0] is True
        assert verify(off_trace.path)[0] is True
        assert os.environ.get("PHOENIX_TRACE_GATE_DECISION") is None


def test_new_run_trace_contains_gate_decision_and_verifies(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("PHOENIX_TRACE_GATE_DECISION", raising=False)
    spec = _p3()
    trace = HackathonTrace("gate-new", tmp_path / "logs")
    judged = _judge(tmp_path / "cache", spec, trace)
    assert judged[0] == "CANDIDATE"
    assert "must beat hionay/tailer" in judged[1]
    trace.seal()
    ok, run_hash = verify(trace.path)
    assert ok is True
    assert run_hash == trace.run_hash
    lines = [json.loads(line) for line in trace.path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines[-1]["action"] == "RUN_SEAL"
    gates = [line for line in lines if line.get("action") == "GATE_DECISION"]
    assert len(gates) == 1
    index = lines.index(gates[0])
    assert index > 0
    assert gates[0]["prev_hash"] == lines[index - 1]["line_hash"]
    assert gates[0]["line_hash"] == line_hash(gates[0])
    assert lines[index + 1]["prev_hash"] == gates[0]["line_hash"]
    body = _payload(gates[0])
    assert body["path"] == "P3"
    assert body["raw_verdict"] == "ALREADY_EXISTS"
    assert body["final_verdict"] == "CANDIDATE"
    assert body["gate"]["checks"]["B"] == "fail"
    assert body["gate"]["checks"]["C"] == "fail"


def test_committed_traces_without_gate_decision_still_verify():
    manifest = json.loads((ROOT / "data" / "replays" / "manifest.json").read_text(encoding="utf-8"))
    checked = 0
    for row in manifest["replays"]:
        if row.get("trace_class") != "RECORDED":
            continue
        directory = ROOT / "data" / "replays" / row["id"]
        trace = directory / "trace.jsonl"
        record = json.loads((directory / "record.json").read_text(encoding="utf-8"))
        expected = record["recorded_run"]["run_hash"]
        text = trace.read_text(encoding="utf-8")
        assert "GATE_DECISION" not in text
        ok, detail = verify(trace, expected_run_hash=expected)
        assert ok, (row["id"], detail)
        assert detail == expected
        checked += 1
    assert checked >= 16
    rescue = ROOT / "docs" / "rescue" / "trace-rescue-0xmh__fichero-printer-20261027T001117Z.jsonl"
    assert "GATE_DECISION" not in rescue.read_text(encoding="utf-8")
    ok, detail = verify(
        rescue,
        expected_run_hash="51e828e538ed8f05dda15648fc673c3461ca310a87c0af77c53482e1f0a086c4",
    )
    assert ok, detail
