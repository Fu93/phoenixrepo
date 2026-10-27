"""Replay catalog for GET /ui.

Facts shown for a replay are copied from files already in the repository.
``trace_class: SAMPLE`` means the JSONL was sealed in-repo (over those copies,
or by a local ``revive()`` call). It is not a Nightshift log.
``trace_class: RECORDED`` means the JSONL was copied byte for byte from a real
trace. 19 RECORDED traces are committed under ``data/replays/``: the 16 v3 case
traces, the Zetaris-backed gate 1 trace, and two Experiment E3 replays that are
not the scored run. ``logs/`` is gitignored, so a trace that was not imported
is not in a checkout.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from app.agents.rescue import from_candidate_row
from config.settings import model_mismatch
from scripts.verify_trace import verify

ROOT = Path(__file__).resolve().parents[1]
REPLAYS_ROOT = ROOT / "data" / "replays"
UI_FILE = ROOT / "app" / "ui" / "index.html"
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
_FLAG_NAMES = ("MODEL_MISMATCH", "SOURCE_ERROR", "DISCARDED_BY_POLICY", "CALL_FAILED")
_UNUSED_ANSWER = {"error", "rejected"}


class ReplayError(Exception):
    def __init__(self, message: str, status: int = 404) -> None:
        super().__init__(message)
        self.status = status


def ui_path() -> Path:
    return UI_FILE


def catalog() -> dict[str, Any]:
    return json.loads((REPLAYS_ROOT / "manifest.json").read_text(encoding="utf-8"))


def live_status(settings: Any) -> dict[str, Any]:
    """Whether POST /judge can call Nightshift. The token value is never returned."""

    token = getattr(settings, "meterless_runtime_token", None)
    if token and token != "rt-local":
        return {
            "enabled": True,
            "reason": (
                "A Nightshift token is configured. The box below calls POST /judge, "
                "which needs the network and can take several minutes. The token is not shown."
            ),
        }
    return {
        "enabled": False,
        "reason": (
            "No Nightshift token is configured. POST /judge returns HTTP 503 and writes no verdict. "
            "Set METERLESS_RUNTIME_TOKEN to a real token. A placeholder such as rt-local does not count. "
            "Replay mode does not use a token, the network, or Docker."
        ),
    }


def replay_payload(replay_id: str) -> dict[str, Any]:
    directory = _directory(replay_id)
    record = json.loads((directory / "record.json").read_text(encoding="utf-8"))
    trace_path = directory / "trace.jsonl"
    steps: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    if trace_path.is_file():
        events = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        steps = [step_from_event(event, index) for index, event in enumerate(events, start=1)]
        verification = _verify_replay(trace_path, record, replay_id)
    else:
        verification = {
            "ok": False,
            "state": "fail",
            "compared": False,
            "detail": "no trace file is committed for this replay",
        }
    verdict = dict(record["verdict"])
    computed = _rescue(record.get("rescue_inputs"))
    if computed is not None:
        verdict["rescue"] = computed
    return {
        "id": record["id"],
        "title": record["title"],
        "repo_id": record["repo_id"],
        "repo_url": record.get("repo_url") or "",
        "data_class": record["data_class"],
        "trace_class": record["trace_class"],
        "banner": record["banner"],
        "model_verdict_recorded": bool(record.get("model_verdict_recorded")),
        "sources": record.get("sources") or [],
        "recorded_run": record.get("recorded_run"),
        "steps": steps,
        "models": model_summary(steps),
        "verdict": verdict,
        "verdict_reasoning": verdict_reasoning(events, str(record.get("repo_id") or "")),
        "verification": verification,
    }


def verdict_reasoning(events: list[dict[str, Any]], repo_id: str) -> dict[str, Any] | None:
    """The reasoning field on the used discovery line, when that field is present.

    Only a used answer counts (not one a Nemotron-only policy discarded). The latest one wins,
    because a rerun can follow an infrastructure failure.
    """
    found: dict[str, Any] | None = None
    for index, event in enumerate(events, start=1):
        if str(event.get("action") or "") != "LLM_CALL" or str(event.get("status") or "") in {"rejected", "error"}:
            continue
        inputs = _as_object(event.get("input_summary")) or {}
        output = _as_object(event.get("output_summary")) or {}
        if inputs.get("step") != "discovery" or (repo_id and inputs.get("repo_id") not in {repo_id, None}):
            continue
        text = output.get("reasoning")
        if not isinstance(text, str) or not text.strip():
            continue
        served = event.get("served_model") or output.get("served_model") or ""
        found = {
            "text": _clip(text, 1500),
            "served_model": str(served or ""),
            "provider": str(output.get("provider") or "nightshift"),
            "nemotron": not model_mismatch(served),
            "step_index": index,
        }
    return found


# Same fact as the replay page and the README: a SAMPLE file is sealed in this
# repository over committed copies. It is not a trace copied from a run.
SAMPLE_NOTE = (
    "A SAMPLE replay is a reconstruction sealed over committed files, not a trace copied from a run."
)
SAMPLE_DETAIL = "sample, not a recorded run: hash not compared"


def recorded_run_hash(record: dict[str, Any]) -> str | None:
    """``recorded_run.run_hash`` when record.json has a non-empty string. Otherwise none."""

    recorded = record.get("recorded_run")
    if not isinstance(recorded, dict):
        return None
    value = recorded.get("run_hash")
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def manifest_trace_class(replay_id: str) -> str | None:
    """``trace_class`` from ``data/replays/manifest.json`` for this id, when listed."""

    path = REPLAYS_ROOT / "manifest.json"
    if not path.is_file():
        return None
    try:
        listed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    for row in listed.get("replays") or []:
        if isinstance(row, dict) and row.get("id") == replay_id:
            value = row.get("trace_class")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _verify_replay(trace_path: Path, record: dict[str, Any], replay_id: str) -> dict[str, Any]:
    """Check a RECORDED trace against ``recorded_run.run_hash`` when that field is set.

    A SAMPLE entry in the manifest is not compared. The page shows a neutral line,
    not a pass and not a failure. A RECORDED replay with no hash is not a pass.
    A RECORDED chain that still matches after a re-seal fails when the new run hash differs.
    """

    trace_class = manifest_trace_class(replay_id)
    if trace_class is None:
        raw = record.get("trace_class")
        trace_class = raw.strip() if isinstance(raw, str) else ""
    if trace_class == "SAMPLE":
        return {
            "ok": False,
            "state": "sample",
            "compared": False,
            "detail": SAMPLE_DETAIL,
            "note": SAMPLE_NOTE,
        }
    expected = recorded_run_hash(record)
    if expected is None:
        chain_ok, chain_detail = verify(trace_path)
        detail = "no recorded hash to compare"
        if not chain_ok:
            detail = f"{detail}; {chain_detail}"
        return {"ok": False, "state": "no_recorded_hash", "compared": False, "detail": detail}
    ok, detail = verify(trace_path, expected_run_hash=expected)
    return {
        "ok": ok,
        "state": "pass" if ok else "fail",
        "compared": True,
        "detail": detail,
        "expected_run_hash": expected,
    }


def tamper_replay(replay_id: str) -> dict[str, Any]:
    """Verify a one-character copy. The committed trace is not modified."""

    directory = _directory(replay_id)
    path = directory / "trace.jsonl"
    if not path.is_file():
        raise ReplayError("this replay has no trace file to tamper", 409)
    original = path.read_text(encoding="utf-8")
    flipped = flip_one_character(original)
    handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    try:
        handle.write(flipped)
        handle.close()
        ok, detail = verify(Path(handle.name))
    finally:
        os.unlink(handle.name)
    return {
        "ok": ok,
        "detail": detail,
        "changed": "one character in a server copy",
        "file_modified": False,
    }


def flip_one_character(text: str) -> str:
    chars = list(text)
    for index, char in enumerate(chars):
        if char.isascii() and char.isalpha():
            chars[index] = "b" if char != "b" else "c"
            return "".join(chars)
    raise ReplayError("trace has no letter to flip", 409)


def step_from_event(event: dict[str, Any], index: int) -> dict[str, Any]:
    """One timeline row. Seconds and flags come only from fields on the event."""

    parsed = [_as_object(event.get("input_summary")), _as_object(event.get("output_summary"))]
    payloads = [item for item in parsed if isinstance(item, dict)]
    requested = _model(event, "requested_model", payloads)
    served = _model(event, "served_model", payloads)
    flags: set[str] = set()
    status = str(event.get("status") or "")
    if status in _FLAG_NAMES:
        flags.add(status)
    _take_flags(event.get("flags"), flags)
    for payload in payloads:
        _collect_flags(payload, flags)
    # Same rule as the trace writer: missing or non-Nemotron served models mismatch
    # only when the answer was used. error/rejected lines keep the flag stored on
    # the line (CALL_FAILED, DISCARDED_BY_POLICY, or a historical MODEL_MISMATCH).
    # A different nvidia/nemotron-* size does not. requested != served is not enough.
    llm_line = "requested_model" in event or "served_model" in event
    if status not in _UNUSED_ANSWER:
        if "served_model" in event:
            if model_mismatch(event.get("served_model")):
                flags.add("MODEL_MISMATCH")
        elif served and model_mismatch(served):
            flags.add("MODEL_MISMATCH")
    output = parsed[1] if isinstance(parsed[1], dict) else {}
    reasoning = output.get("reasoning") if isinstance(output.get("reasoning"), str) else ""
    return {
        "index": index,
        "agent": str(event.get("agent_name") or ""),
        "action": str(event.get("action") or event.get("event") or ""),
        "requested_model": requested,
        "served_model": served,
        "provider": str(output.get("provider") or ("nightshift" if llm_line else "")),
        "tier": str(output.get("tier") or ""),
        "reasoning": _clip(reasoning, 1500),
        "seconds": _seconds(payloads),
        "status": status,
        "flags": sorted(flags),
        "llm_line": llm_line,
        "timestamp": event.get("timestamp") or None,
        "input_summary": _clip(_text(event.get("input_summary"))),
        "output_summary": _clip(_text(event.get("output_summary"))),
    }


def model_summary(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """Served-model counts from LLM lines. An empty served id is ``(missing)``."""

    calls: dict[str, int] = {}
    mismatches = discarded = failed = used = nemotron = 0
    for step in steps:
        if not step.get("llm_line"):
            continue
        label = step.get("served_model") or "(missing)"
        calls[label] = calls.get(label, 0) + 1
        flags = step.get("flags") or []
        if "CALL_FAILED" in flags:
            failed += 1
        if "DISCARDED_BY_POLICY" in flags:
            discarded += 1
            continue
        if "MODEL_MISMATCH" in flags and "CALL_FAILED" not in flags:
            mismatches += 1
        if label != "(missing)" and step.get("status") not in {"error", "rejected"}:
            used += 1
            if label.startswith("nvidia/nemotron-"):
                nemotron += 1
    return {
        "model_mismatch_count": mismatches,
        "call_failed_count": failed,
        "model_calls": calls,
        "discarded_by_policy": discarded,
        "answers_used": used,
        "answers_by_nemotron": nemotron,
    }


def _directory(replay_id: str) -> Path:
    if not _ID.match(replay_id):
        raise ReplayError("unknown replay", 404)
    root = REPLAYS_ROOT.resolve()
    directory = (REPLAYS_ROOT / replay_id).resolve()
    if directory.parent != root or not directory.is_dir():
        raise ReplayError("unknown replay", 404)
    return directory


def _rescue(inputs: dict[str, Any] | None) -> dict[str, Any] | None:
    if not inputs:
        return None
    scored = from_candidate_row(
        {
            "stargazers_count": inputs.get("stargazers_count"),
            "forks_count": inputs.get("forks_count"),
            "open_issues": inputs.get("open_issues"),
            "pushed_at": inputs.get("pushed_at"),
        },
        as_of=date.fromisoformat(str(inputs["as_of"])),
    )
    return {
        "score": scored["score"],
        "changes_verdict": scored["changes_verdict"],
        "components": scored["components"],
        "as_of": inputs["as_of"],
        "inputs": {
            "stargazers_count": inputs.get("stargazers_count"),
            "forks_count": inputs.get("forks_count"),
            "open_issues": inputs.get("open_issues"),
            "pushed_at": inputs.get("pushed_at"),
            "days_since_push": inputs.get("days_since_push"),
        },
        "inputs_source": inputs.get("source"),
        "in_source_score_file": False,
        "note": (
            "Computed by app.agents.rescue.from_candidate_row from the discovery row. "
            "docs/revival/venture_scores.json does not contain rescue_potential."
        ),
    }


def _model(event: dict[str, Any], key: str, payloads: list[dict[str, Any]]) -> str | None:
    value = event.get(key)
    if value:
        return str(value)
    for payload in payloads:
        nested = payload.get(key)
        if nested:
            return str(nested)
    return None


def _seconds(payloads: list[dict[str, Any]]) -> float | None:
    for payload in payloads:
        for key in ("elapsed_s", "elapsed_seconds", "seconds"):
            number = _number(payload.get(key))
            if number is not None:
                return number
        millis = _number(payload.get("latency_ms"))
        if millis is not None:
            return millis / 1000.0
    return None


def _take_flags(listed: Any, found: set[str]) -> None:
    if not isinstance(listed, list):
        return
    for item in listed:
        if item in _FLAG_NAMES:
            found.add(str(item))


def _collect_flags(value: Any, found: set[str]) -> None:
    if isinstance(value, dict):
        _take_flags(value.get("flags"), found)
        source_status = value.get("source_status")
        if isinstance(source_status, dict):
            for status in source_status.values():
                if status == "SOURCE_ERROR":
                    found.add("SOURCE_ERROR")
        for nested in value.values():
            _collect_flags(nested, found)
        return
    if isinstance(value, list):
        for nested in value:
            _collect_flags(nested, found)
        return
    if value in _FLAG_NAMES:
        found.add(str(value))


def _as_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.startswith("{"):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return None
        if isinstance(loaded, dict):
            return loaded
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _clip(value: str, limit: int = 4000) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
