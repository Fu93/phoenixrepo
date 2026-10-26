"""Replay committed recordings. The page reads files; it does not re-judge.

The repository did not ship a hash-chained JSONL for the venture score run or the
gate-1 audit. ``sealed_trace_text`` seals those committed JSON files with the same
``line_hash`` function as ``scripts/verify_trace.py``. Per-step model, status, and
duration are omitted when the source file does not have them. The HTTP handlers
read the committed JSONL; they do not call this sealer.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from app.trace_log import canonical_line, line_hash

ROOT = Path(__file__).resolve().parents[1]
MISSING = "not recorded"
SCORES_REL = "docs/revival/venture_scores.json"
LABELS_REL = "data/eval/gate1_labels.json"
SEARCH_ROOTS = ("data", "reports", "run_artifacts", "trace", "docs/revival")

_ALT_RE = re.compile(r"^(\S+)\s+\((https?://[^)\s]+)\)")
_PUSH_RE = re.compile(r"pushed\s+([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z)")


def _load(relative: str) -> Any:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _seal(events: list[dict[str, Any]]) -> str:
    previous = ""
    lines: list[str] = []
    for event in events:
        record = dict(event)
        record["prev_hash"] = previous
        record["line_hash"] = line_hash(record)
        previous = str(record["line_hash"])
        lines.append(canonical_line(record))
    return "\n".join(lines) + "\n"


def _line(run_id: str, action: str, output: Any, source_fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": action,
        "event": action,
        "run_id": run_id,
        "input_summary": source_fields,
        "output_summary": output,
    }


def _venture_events(data: dict[str, Any]) -> list[dict[str, Any]]:
    run_id = str(data["run_id"])
    events: list[dict[str, Any]] = []
    for row in data.get("ineligible") or []:
        if isinstance(row, dict):
            events.append(_line(run_id, str(row.get("verdict") or "ineligible"), row, {"repo_id": row.get("repo_id"), "list": "ineligible"}))
    for row in data.get("candidates") or []:
        if isinstance(row, dict):
            events.append(_line(run_id, str(row.get("verdict") or "candidate"), row, {"repo_id": row.get("repo_id"), "list": "candidates"}))
    protagonist = data.get("protagonist")
    if isinstance(protagonist, dict):
        events.append(_line(run_id, "protagonist", protagonist, {"repo_id": protagonist.get("repo_id"), "list": "protagonist"}))
    events.append(
        _line(
            run_id,
            "RUN_SEAL",
            "Final run hash is this line's line_hash.",
            {
                "source": SCORES_REL,
                "recorded_run_hash": data.get("run_hash"),
                "llm_used": data.get("llm_used"),
                "command": data.get("command"),
                "date": data.get("date"),
            },
        )
    )
    return events


def _label_events(data: dict[str, Any]) -> list[dict[str, Any]]:
    run_id = f"gate1-labels-{data.get('fetched_at')}"
    events = [
        _line(run_id, str(case.get("repo_id") or "case"), case, {"source": LABELS_REL, "repo_id": case.get("repo_id")})
        for case in data.get("cases") or []
        if isinstance(case, dict)
    ]
    events.append(
        _line(
            run_id,
            "RUN_SEAL",
            "Final run hash is this line's line_hash.",
            {"source": LABELS_REL, "note": data.get("note"), "fetched_at": data.get("fetched_at")},
        )
    )
    return events


def sealed_trace_text(run_id: str) -> str:
    """Deterministic seal of a committed JSON file. Does not call a model."""

    scores = _load(SCORES_REL)
    if run_id == scores.get("run_id"):
        return _seal(_venture_events(scores))
    labels = _load(LABELS_REL)
    if run_id == f"gate1-labels-{labels.get('fetched_at')}":
        return _seal(_label_events(labels))
    raise KeyError(run_id)


def committed_run_ids() -> list[str]:
    scores = _load(SCORES_REL)
    labels = _load(LABELS_REL)
    return [f"gate1-labels-{labels.get('fetched_at')}", str(scores.get("run_id"))]


def trace_path_for(run_id: str) -> Path:
    return ROOT / "reports" / run_id / f"trace-{run_id}.jsonl"


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _iter_trace_files() -> list[Path]:
    found: list[Path] = []
    for relative in SEARCH_ROOTS:
        root = ROOT / relative
        if not root.exists():
            continue
        for path in root.rglob("*.jsonl"):
            if path.is_file():
                found.append(path)
    found.sort(key=lambda path: _relative(path))
    return found


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            records.append(item)
    return records


def _seal_record(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in reversed(records):
        if record.get("action") == "RUN_SEAL":
            return record
    return {}


def _present_value(value: Any) -> Any:
    if value is None or value == "":
        return MISSING
    return value


def _lookup_model(record: dict[str, Any], key: str) -> Any:
    if record.get(key) not in (None, ""):
        return record[key]
    for bucket_name in ("input_summary", "output_summary"):
        bucket = record.get(bucket_name)
        if isinstance(bucket, dict) and bucket.get(key) not in (None, ""):
            return bucket[key]
        if isinstance(bucket, dict) and isinstance(bucket.get("llm"), dict) and bucket["llm"].get(key) not in (None, ""):
            return bucket["llm"][key]
    return MISSING


def _duration(record: dict[str, Any]) -> Any:
    for key in ("duration_ms", "latency_ms"):
        if record.get(key) not in (None, ""):
            return f"{record[key]} ms"
    for bucket_name in ("input_summary", "output_summary"):
        bucket = record.get(bucket_name)
        if not isinstance(bucket, dict):
            continue
        for key in ("duration_ms", "latency_ms"):
            if bucket.get(key) not in (None, ""):
                return f"{bucket[key]} ms"
        for key in ("duration_s", "elapsed_s"):
            if bucket.get(key) not in (None, ""):
                return f"{bucket[key]} s"
    for key in ("duration_s", "elapsed_s"):
        if record.get(key) not in (None, ""):
            return f"{record[key]} s"
    return MISSING


def _alternative(item: dict[str, Any]) -> dict[str, Any]:
    name = item.get("alternative_id") or item.get("alternative") or item.get("name")
    last_push = item.get("pushed_at") if item.get("pushed_at") not in (None, "") else item.get("last_push")
    url = item.get("url")
    stars = item["stars"] if "stars" in item and item.get("stars") is not None else MISSING
    return {
        "name": _present_value(name),
        "stars": stars,
        "last_push": _present_value(last_push),
        "url": _present_value(url),
    }


def _reason_alternative(reason: str) -> dict[str, Any] | None:
    match = _ALT_RE.match(reason.strip())
    if not match:
        return None
    pushed = _PUSH_RE.search(reason)
    return {
        "name": match.group(1),
        "stars": MISSING,
        "last_push": pushed.group(1) if pushed else MISSING,
        "url": match.group(2),
    }


def _quotes_and_verified(output: dict[str, Any]) -> tuple[Any, Any]:
    quotes: list[str] = []
    checks: list[bool] = []
    evidence = output.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict):
                continue
            quote = item.get("quote")
            if isinstance(quote, str) and quote.strip():
                quotes.append(quote)
                text = item.get("text")
                if isinstance(text, str):
                    checks.append(quote in text)
    if not quotes:
        for item in output.get("alternatives") or []:
            if not isinstance(item, dict):
                continue
            for quote in item.get("quotes") or []:
                if isinstance(quote, str) and quote.strip():
                    quotes.append(quote)
            one = item.get("quote")
            if isinstance(one, str) and one.strip():
                quotes.append(one)
    if not quotes:
        return MISSING, MISSING
    if not checks:
        return quotes, MISSING
    if all(checks):
        return quotes, "substring of stored text"
    return quotes, "not a substring of stored text"


def _source_error(output: dict[str, Any]) -> Any:
    found: list[str] = []
    if "fetch_status" in output and output.get("fetch_status") not in (None, ""):
        found.append(str(output.get("fetch_status")))
    audit = output.get("audit")
    blocker = audit.get("blocker") if isinstance(audit, dict) else None
    if isinstance(blocker, dict) and blocker.get("fetch_status") not in (None, ""):
        found.append(str(blocker.get("fetch_status")))
    status = output.get("source_status")
    if isinstance(status, dict) and status:
        found.extend(f"{key}={value}" for key, value in sorted(status.items()))
    if not found:
        return MISSING
    if any(item == "source_error" or item.endswith("=source_error") or item.endswith("=SOURCE_ERROR") for item in found):
        return "source_error"
    return ", ".join(found)


def _rescue(output: dict[str, Any], action: str) -> tuple[Any, Any]:
    rescue = output.get("rescue_potential") if isinstance(output.get("rescue_potential"), dict) else None
    if rescue is None and action == "RESCUE_POTENTIAL" and "score" in output:
        rescue = output
    if rescue is None:
        return MISSING, MISSING
    score = rescue["score"] if "score" in rescue and rescue.get("score") is not None else MISSING
    components = rescue["components"] if isinstance(rescue.get("components"), dict) else MISSING
    return score, components


def _card(record: dict[str, Any]) -> dict[str, Any]:
    output = record.get("output_summary")
    action = str(record.get("action") or "")
    card: dict[str, Any] = {
        "verdict": MISSING,
        "reason": MISSING,
        "alternatives": MISSING,
        "quotes": MISSING,
        "verified": MISSING,
        "source_error": MISSING,
        "rescue_score": MISSING,
        "rescue_components": MISSING,
    }
    if isinstance(output, str) and output.strip():
        card["reason"] = output
        return card
    if not isinstance(output, dict):
        return card
    if output.get("verdict") not in (None, ""):
        card["verdict"] = output.get("verdict")
    if isinstance(output.get("reason"), str) and output.get("reason"):
        card["reason"] = output["reason"]
    elif isinstance(output.get("why"), str) and output.get("why"):
        card["reason"] = output["why"]
    alternatives: list[dict[str, Any]] = []
    audit = output.get("audit")
    blocker = audit.get("blocker") if isinstance(audit, dict) else None
    if isinstance(blocker, dict):
        alternatives.append(_alternative(blocker))
    for item in output.get("decision_evidence") or []:
        if isinstance(item, dict):
            alternatives.append(_alternative(item))
    if not alternatives and isinstance(output.get("alternatives"), list):
        alternatives.extend(_alternative(item) for item in output["alternatives"] if isinstance(item, dict))
    if not alternatives and card["verdict"] == "ALREADY_EXISTS" and isinstance(card["reason"], str):
        parsed = _reason_alternative(card["reason"])
        if parsed:
            alternatives.append(parsed)
    if alternatives:
        card["alternatives"] = alternatives
    quotes, verified = _quotes_and_verified(output)
    card["quotes"] = quotes
    card["verified"] = verified
    card["source_error"] = _source_error(output)
    card["rescue_score"], card["rescue_components"] = _rescue(output, action)
    if isinstance(output.get("acceptable"), list):
        card["acceptable"] = output["acceptable"]
    if isinstance(output.get("forbidden"), list):
        card["forbidden"] = output["forbidden"]
    if isinstance(audit, dict) and audit.get("class"):
        card["audit_class"] = audit["class"]
    return card


def _repo_id(record: dict[str, Any]) -> str | None:
    output = record.get("output_summary")
    if isinstance(output, dict) and isinstance(output.get("repo_id"), str) and output.get("repo_id"):
        return str(output["repo_id"])
    source = record.get("input_summary")
    if isinstance(source, dict) and isinstance(source.get("repo_id"), str) and source.get("repo_id"):
        return str(source["repo_id"])
    return None


def _present_step(index: int, record: dict[str, Any]) -> dict[str, Any]:
    step: dict[str, Any] = {
        "index": index,
        "event": _present_value(record.get("action") or record.get("event")),
        "agent": _present_value(record.get("agent_name")),
        "status": _present_value(record.get("status")),
        "requested_model": _lookup_model(record, "requested_model"),
        "served_model": _lookup_model(record, "served_model"),
        "duration": _duration(record),
        "timestamp": _present_value(record.get("timestamp")),
        "card": _card(record),
    }
    repo_id = _repo_id(record)
    if repo_id:
        step["repo_id"] = repo_id
    return step


def _start_index(steps: list[dict[str, Any]]) -> int:
    for step in steps:
        card = step["card"]
        if card.get("verdict") == "ALREADY_EXISTS":
            return int(step["index"])
    for step in steps:
        if isinstance(step["card"].get("alternatives"), list):
            return int(step["index"])
    return 0


def _catalog() -> dict[str, dict[str, Any]]:
    runs: dict[str, dict[str, Any]] = {}
    for path in _iter_trace_files():
        try:
            records = _read_jsonl(path)
        except (OSError, json.JSONDecodeError):
            continue
        if not records or "line_hash" not in records[0]:
            continue
        run_id = str(records[0].get("run_id") or "")
        if not run_id or run_id in runs:
            continue
        seal = _seal_record(records)
        source_fields = seal.get("input_summary") if isinstance(seal.get("input_summary"), dict) else {}
        runs[run_id] = {
            "id": run_id,
            "path": path,
            "records": records,
            "source": str(source_fields.get("source") or _relative(path)),
            "seal_input": source_fields,
            "trace_seal": str(seal.get("line_hash") or ""),
        }
    return runs


def list_runs() -> list[dict[str, Any]]:
    catalog = _catalog()
    preferred = committed_run_ids()[0]
    rows = []
    for run_id, item in catalog.items():
        rows.append(
            {
                "id": run_id,
                "source": item["source"],
                "trace": _relative(item["path"]),
                "steps": len(item["records"]),
                "default": run_id == preferred,
            }
        )
    rows.sort(key=lambda row: (not row["default"], row["id"]))
    return rows


def run_detail(run_id: str) -> dict[str, Any] | None:
    item = _catalog().get(run_id)
    if item is None:
        return None
    steps = [_present_step(index, record) for index, record in enumerate(item["records"])]
    source_fields = item["seal_input"]
    detail: dict[str, Any] = {
        "id": run_id,
        "source": item["source"],
        "trace": _relative(item["path"]),
        "trace_seal": item["trace_seal"],
        "trace_text": item["path"].read_text(encoding="utf-8"),
        "note": _present_value(source_fields.get("note")),
        "recorded_run_hash": _present_value(source_fields.get("recorded_run_hash")),
        "llm_used": source_fields["llm_used"] if "llm_used" in source_fields else MISSING,
        "date": _present_value(source_fields.get("date") or source_fields.get("fetched_at")),
        "command": _present_value(source_fields.get("command")),
        "steps": steps,
        "start_index": _start_index(steps),
    }
    return detail


def verify_text(text: str) -> tuple[bool, str]:
    from scripts.verify_trace import verify

    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".jsonl", delete=False)
    try:
        handle.write(text)
        handle.close()
        return verify(Path(handle.name))
    finally:
        os.unlink(handle.name)


def verify_run(run_id: str, text: str | None = None) -> dict[str, Any] | None:
    item = _catalog().get(run_id)
    if item is None:
        return None
    if text is None:
        ok, detail = verify_text(item["path"].read_text(encoding="utf-8"))
    else:
        ok, detail = verify_text(text)
    return {"ok": ok, "result": "PASS" if ok else "FAIL", "detail": detail}


def tamper_text(text: str) -> tuple[str, int, str, str]:
    chars = list(text)
    for index, char in enumerate(chars):
        if "a" <= char <= "z":
            chars[index] = char.upper()
            return "".join(chars), index, char, chars[index]
        if "A" <= char <= "Z":
            chars[index] = char.lower()
            return "".join(chars), index, char, chars[index]
    return text + " ", len(text), "", " "


def tamper_run(run_id: str) -> dict[str, Any] | None:
    item = _catalog().get(run_id)
    if item is None:
        return None
    original = item["path"].read_text(encoding="utf-8")
    mutated, index, before, after = tamper_text(original)
    checked = verify_text(mutated)
    return {
        "ok": checked[0],
        "result": "PASS" if checked[0] else "FAIL",
        "detail": checked[1],
        "trace": mutated,
        "changed_index": index,
        "before": before,
        "after": after,
        "file_unchanged": item["path"].read_text(encoding="utf-8") == original,
    }


def live_judge_status() -> dict[str, Any]:
    from api.run import get_settings
    from config.settings import usable_secret

    token = usable_secret(get_settings().meterless_runtime_token)
    if token:
        return {
            "configured": True,
            "message": "A Nightshift token is configured. Live judge will POST /judge.",
        }
    return {
        "configured": False,
        "message": "No Nightshift token is configured. Live judge is off. Replay does not call Nightshift.",
    }


def ui_html() -> str:
    return (ROOT / "app" / "ui" / "replay.html").read_text(encoding="utf-8")
