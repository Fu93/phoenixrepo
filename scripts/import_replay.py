"""Copy a real trace and its verdict into data/replays.

The JSONL is copied byte for byte, so ``verify_trace`` still checks the
original chain. The verdict row is read from a score_candidates JSON file
(``candidates`` plus ``ineligible``) or from one ``{repo_id, verdict}``
object. The manifest is updated. Nothing in the catalog is hand-edited.

    python scripts/import_replay.py logs/trace-<run_id>.jsonl --verdict path/to/scores.json

When the verdict file lists more than one repository, pass ``--repo owner/name``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.replay_ui import REPLAYS_ROOT  # noqa: E402
from scripts.verify_trace import verify  # noqa: E402

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")


class ImportError(Exception):
    """The trace or verdict could not be copied into the replay catalog."""


def import_replay(
    trace: Path,
    verdict: Path,
    *,
    repo: str | None = None,
    replay_id: str | None = None,
    replays_root: Path | None = None,
) -> dict[str, Any]:
    """Copy ``trace`` unchanged and add one verdict row to the replay manifest."""

    trace = Path(trace)
    verdict = Path(verdict)
    root = Path(replays_root) if replays_root is not None else REPLAYS_ROOT
    if not trace.is_file():
        raise ImportError(f"trace file not found: {trace}")
    ok, detail = verify(trace)
    if not ok:
        raise ImportError(detail)
    if not verdict.is_file():
        raise ImportError(f"verdict file not found: {verdict}")
    try:
        data = json.loads(verdict.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ImportError(f"verdict file is not JSON: {exc}") from exc
    rows = _rows(data)
    row = _select(rows, repo)
    chosen_id = replay_id or _slug(str(row.get("repo_id") or ""))
    if not _ID.match(chosen_id):
        raise ImportError(f"replay id {chosen_id!r} is not a safe catalog id")
    trace_text = trace.read_text(encoding="utf-8")
    leak = secret_in_trace(trace_text)
    if leak:
        # The bytes are copied unchanged so the chain still verifies; a secret cannot be
        # redacted here without breaking it. Refuse instead of committing it.
        raise ImportError(f"trace contains {leak}; it was not imported. Re-run the judgement with the current code, which redacts it.")
    record = _record(chosen_id, row, data, verdict, trace_text)
    directory = root / chosen_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "trace.jsonl").write_bytes(trace.read_bytes())
    (directory / "record.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    entry = {
        "id": chosen_id,
        "title": record["title"],
        "repo_id": record["repo_id"],
        "data_class": "RECORDED",
        "trace_class": "RECORDED",
        "summary": record["summary"],
    }
    _upsert_manifest(root, entry)
    return entry


def secret_in_trace(text: str) -> str:
    """Name what would leak if this trace were committed, or "" when nothing does."""
    from app.trace_log import _TOKEN_PATTERNS
    from config.settings import trace_secrets

    if _TOKEN_PATTERNS.search(text):
        return "a token-shaped string (GitHub, NVIDIA, Anthropic or xAI key format)"
    for value in trace_secrets():
        if len(value) >= 8 and value in text:
            return "a credential from this machine's environment or .env"
    return ""


def _rows(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        raise ImportError("verdict file must be a JSON object")
    if "ineligible" in data or "candidates" in data:
        rows = list(data.get("candidates") or []) + list(data.get("ineligible") or [])
        return [row for row in rows if isinstance(row, dict) and row.get("repo_id")]
    if data.get("repo_id") and data.get("verdict"):
        return [data]
    raise ImportError("verdict file has no repository rows")


def _select(rows: list[dict[str, Any]], repo: str | None) -> dict[str, Any]:
    if not rows:
        raise ImportError("verdict file has no repository rows")
    chosen = str(repo or "").strip()
    if not chosen:
        if len(rows) == 1:
            return rows[0]
        raise ImportError("pass --repo owner/name; the verdict file has more than one repository")
    matches = [row for row in rows if str(row.get("repo_id") or "") == chosen]
    if len(matches) != 1:
        raise ImportError(f"verdict file has no single row for {chosen}")
    return matches[0]


def _slug(repo_id: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", repo_id.strip().lower()).strip("-")
    if not text:
        raise ImportError("repository id is empty")
    return text[:81]


def _record(replay_id: str, row: dict[str, Any], data: dict[str, Any], verdict: Path, trace_text: str) -> dict[str, Any]:
    repo_id = str(row.get("repo_id") or "")
    verdict_name = str(row.get("verdict") or "")
    evidence = [item for item in (row.get("decision_evidence") or []) if isinstance(item, dict)]
    first = evidence[0] if evidence else {}
    quote = str(first.get("quote") or "")
    alternatives = [_alternative(item) for item in evidence]
    stored_rescue = row.get("rescue_potential") if isinstance(row.get("rescue_potential"), dict) else None
    card: dict[str, Any] = {
        "verdict": verdict_name,
        "verdict_kind": "recorded_score",
        "reason": str(row.get("reason") or ""),
        "quote": quote,
        "quote_source_url": str(first.get("url") or ""),
        "quote_source_path": str(verdict),
        "alternatives": alternatives,
        "acceptable": None,
        "gate2": _gate2(trace_text),
    }
    if stored_rescue is not None:
        rescue = dict(stored_rescue)
        rescue.setdefault(
            "note",
            "Stored rescue_potential from the verdict file. Not recomputed for this replay.",
        )
        rescue.setdefault("inputs_source", str(verdict))
        card["rescue"] = rescue
    run = data if data is not row else {}
    return {
        "id": replay_id,
        "title": f"{repo_id} — recorded {verdict_name}",
        "repo_id": repo_id,
        "repo_url": str(row.get("repo_url") or ""),
        "data_class": "RECORDED",
        "trace_class": "RECORDED",
        "banner": (
            "RECORDED trace. The JSONL was copied unchanged from the source file. "
            "The hash chain is the original chain, not a SAMPLE seal."
        ),
        "summary": f"Copied unchanged from a trace file. Verdict {verdict_name} for {repo_id}.",
        "model_verdict_recorded": bool(verdict_name),
        "sources": [str(verdict)],
        "recorded_run": {
            "run_id": run.get("run_id"),
            "run_hash": run.get("run_hash"),
            "llm_used": run.get("llm_used"),
            "source": str(verdict),
            "jsonl_committed": True,
        },
        "verdict": card,
    }


def _alternative(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": str(item.get("alternative") or ""),
        "url": str(item.get("url") or ""),
        "overlap": item.get("overlap") or "",
        "quote": str(item.get("quote") or ""),
        "note": str(item.get("reason") or ""),
    }


def _gate2(trace_text: str) -> dict[str, Any]:
    """Read a gate-2 state only when the copied trace actually recorded one."""

    blocked = False
    blocked_reason = ""
    result_state = None
    result_reason = ""
    for line in trace_text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        action = str(event.get("action") or event.get("event") or "")
        output = _object(event.get("output_summary"))
        if action == "REVIVE_BLOCKED_BY_GATE1":
            blocked = True
            blocked_reason = str((output or {}).get("note") or (output or {}).get("reason") or "")
        elif action == "REVIVE_RESULT" and isinstance(output, dict) and output.get("state"):
            result_state = str(output["state"])
            result_reason = str(output.get("reason") or "")
    if blocked:
        why = blocked_reason or "The copied trace records REVIVE_BLOCKED_BY_GATE1."
        return {
            "state": "NOT_ATTEMPTED_GATE1",
            "historical_trace": True,
            "reason": why,
            "why": why,
            "rule_quote": "",
            "rule_source": "",
        }
    if result_state:
        return {
            "state": result_state,
            "historical_trace": True,
            "reason": result_reason,
            "why": result_reason,
            "rule_quote": "",
            "rule_source": "",
        }
    return {
        "state": None,
        "historical_trace": False,
        "why": "The copied trace does not record REVIVE_BLOCKED_BY_GATE1 or a revive result.",
        "rule_quote": "",
        "rule_source": "",
    }


def _object(value: Any) -> dict[str, Any] | None:
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


def _upsert_manifest(root: Path, entry: dict[str, Any]) -> None:
    path = root / "manifest.json"
    if path.is_file():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ImportError("manifest.json is not an object")
    else:
        manifest = {"replays": [], "missing": []}
    replays = [item for item in (manifest.get("replays") or []) if item.get("id") != entry["id"]]
    replays.append(entry)
    manifest["replays"] = replays
    manifest.setdefault("missing", [])
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy a real trace and its verdict into data/replays.")
    parser.add_argument("trace", type=Path, help="logs/trace-<run_id>.jsonl")
    parser.add_argument("--verdict", type=Path, required=True, help="score_candidates JSON, or one {repo_id, verdict} object")
    parser.add_argument("--repo", default=None, help="owner/name when the verdict file has more than one repository")
    parser.add_argument("--id", dest="replay_id", default=None, help="catalog id (default: slug of the repository)")
    parser.add_argument("--replays-dir", type=Path, default=None, help="catalog directory (default: data/replays)")
    args = parser.parse_args(argv)
    try:
        entry = import_replay(
            args.trace,
            args.verdict,
            repo=args.repo,
            replay_id=args.replay_id,
            replays_root=args.replays_dir,
        )
    except ImportError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(entry["id"])
    return 0


if __name__ == "__main__":
    from app.console import ensure_utf8_stdio

    ensure_utf8_stdio()
    raise SystemExit(main())
