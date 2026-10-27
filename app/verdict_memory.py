"""Gate-1 verdict memory on Meterless H-MEM.

Every gate-1 verdict is written to an H-MEM store (``app/engines/hmem``) as a long-term memory
with its run id and its reason. The served model is stored when the row carries ``served_model``,
when the reason ends with ``served=<id>``, or when ``served_by_repo`` names that repository.
Otherwise the stored ``served_model`` is empty. Before writing, the store is asked for the
verdicts it already holds for the same repository (the id match is case-insensitive). Each live
one is superseded by the new verdict (H-MEM keeps it, it does not delete), and when they differ
the trace line says so. If supersede fails, the write still tries to leave only one live verdict.

This never changes a verdict. It makes one known weakness visible on the record: generated
search queries can move a verdict between runs (CalendarSyncProject has had three). A repeat
judgement that disagrees with what H-MEM remembers carries ``verdict_changed: true`` on its
``HMEM_VERDICT_MEMORY`` line, so a person can look before anyone acts on it.

The store is a JSON file next to the trace logs (``<log_dir>/hmem/gate1_verdicts.json``), with
the append-only trust ledger beside it. ``PHOENIX_HMEM=off`` turns it off. Any H-MEM error is
recorded on the trace line and gate 1 carries on.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from app.engines.hmem.ledger import TrustLedgerService
from app.engines.hmem.store import MemoryStoreService


def _store(log_dir: Path) -> MemoryStoreService:
    directory = Path(log_dir) / "hmem"
    directory.mkdir(parents=True, exist_ok=True)
    clock = lambda: int(time.time() * 1000)  # noqa: E731
    ledger = TrustLedgerService(directory / "gate1_ledger.jsonl", clock=clock)
    store = MemoryStoreService(ledger=ledger, path=directory / "gate1_verdicts.json", clock=clock)
    store.hydrate()
    return store


_SERVED_IN_REASON = re.compile(r"served=([^)]*)\)\.?\s*$")


def served_model_from_reason(reason: object) -> str:
    """The ``served=<id>`` at the end of a gate-1 verdict reason, or empty."""

    match = _SERVED_IN_REASON.search(str(reason or ""))
    return match.group(1).strip() if match else ""


def _lookup_served(served_by_repo: dict[str, str] | None, repo_id: str) -> str:
    if not served_by_repo:
        return ""
    if repo_id in served_by_repo:
        return str(served_by_repo[repo_id] or "").strip()
    key = repo_id.casefold()
    for name, value in served_by_repo.items():
        if str(name).casefold() == key:
            return str(value or "").strip()
    return ""


def _served_for(row: dict[str, Any], served_by_repo: dict[str, str] | None, repo_id: str) -> str:
    direct = row.get("served_model")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    from_reason = served_model_from_reason(row.get("reason"))
    if from_reason:
        return from_reason
    return _lookup_served(served_by_repo, repo_id)


def _keep_one_live(store: Any, prior: list[Any], new_id: str) -> None:
    """Supersede every live prior. A failed supersede must not leave two live verdicts."""

    for old in prior:
        try:
            store.supersede(old.id, new_id)
            continue
        except Exception:
            pass
        try:
            kept = store.records.get(old.id)
            fresh = store.records.get(new_id)
            if kept is None or fresh is None:
                raise RuntimeError("verdict record missing after add")
            if not kept.superseded_by:
                kept.superseded_by = new_id
            if not fresh.supersedes:
                fresh.supersedes = old.id
            store._save()
        except Exception:
            store.records.pop(new_id, None)
            for record in list(store.records.values()):
                if getattr(record, "superseded_by", None) == new_id:
                    record.superseded_by = None
            try:
                store._save()
            except Exception:
                pass
            raise


def _current(records: list[Any]) -> list[Any]:
    """Verdict memories not yet superseded, newest first."""
    live = [r for r in records if not r.superseded_by]
    return sorted(live, key=lambda r: r.timestamp, reverse=True)


def _trace_error(trace: Any, *, run_id: str = "", repo_id: str = "", exc: BaseException) -> None:
    """Record an H-MEM failure. A broken trace must not escape into gate 1."""

    try:
        trace.event(
            agent_name="hmem",
            action="HMEM_VERDICT_MEMORY",
            input_summary={"run_id": run_id, "repo_id": repo_id},
            output_summary={"error": f"{type(exc).__name__}: {str(exc)[:200]}"},
            status="error",
        )
    except Exception:
        return


def remember(
    rows: list[dict[str, Any]],
    *,
    trace: Any,
    log_dir: Path,
    run_id: str,
    served_by_repo: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Write one H-MEM verdict memory per row and trace what was remembered before.

    ``rows`` are ``{"repo_id", "verdict", "reason"}``. Returns the per-repo summaries it traced.
    This never raises and never writes back into ``rows``.
    """
    if os.environ.get("PHOENIX_HMEM", "").strip().lower() in {"off", "0", "false", "no"}:
        return []
    try:
        store = _store(log_dir)
    except Exception as exc:  # H-MEM must never stop gate 1
        _trace_error(trace, run_id=run_id, exc=exc)
        return []
    try:
        return _remember_rows(store, rows, trace=trace, run_id=run_id, served_by_repo=served_by_repo)
    except Exception as exc:
        _trace_error(trace, run_id=run_id, exc=exc)
        return []


def _remember_rows(
    store: Any,
    rows: list[dict[str, Any]],
    *,
    trace: Any,
    run_id: str,
    served_by_repo: dict[str, str] | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    # One lock covers recall, add, supersede and the conflict line, so a second
    # gate-1 run cannot replace the JSON store between those steps.
    with store.exclusive():
        for row in rows:
            repo_id = ""
            try:
                if not isinstance(row, dict):
                    continue
                repo_id = str(row.get("repo_id") or "").strip()
                verdict = str(row.get("verdict") or "").strip()
                if not repo_id or not verdict:
                    continue
                prior = _current(store.find_verdicts(repo_id))
                history = [{"verdict": str(r.extra.get("verdict") or ""), "run_id": str(r.source)} for r in prior[:5]]
                record = store.add(
                    f"{repo_id} gate-1 verdict {verdict}: {str(row.get('reason') or '')[:400]}",
                    source=run_id,
                    origin="model_response",
                    label="gate1_verdict",
                    layer="long_term",
                    entities=[repo_id],
                    tags=["gate1", verdict],
                    extra={
                        "kind": "verdict",
                        "verdict": verdict,
                        "served_model": _served_for(row, served_by_repo, repo_id),
                    },
                )
                if prior:
                    _keep_one_live(store, prior, record.id)
                    if record.id not in store.records:
                        raise RuntimeError("new verdict was dropped so two live verdicts would not remain")
                changed = bool(prior) and history[0]["verdict"] != verdict
                if changed:
                    store.ledger.append(
                        record.id,
                        "conflict_detected",
                        previous_state={"verdict": history[0]["verdict"], "run_id": history[0]["run_id"]},
                        new_state={"verdict": verdict, "run_id": run_id},
                        details={"repo_id": repo_id, "kept": "both; the older one is superseded, not deleted"},
                    )
                summary = {
                    "repo_id": repo_id,
                    "verdict": verdict,
                    "memory_id": record.id,
                    "remembered_before": history,
                    "verdict_changed": changed,
                    "note": (
                        f"H-MEM remembered {history[0]['verdict']} from run {history[0]['run_id']}; "
                        "this run disagrees, so a person should look before acting on it."
                        if changed else
                        ("H-MEM holds the same verdict from an earlier run." if prior else "First verdict H-MEM holds for this repository.")
                    ),
                }
                trace.event(agent_name="hmem", action="HMEM_VERDICT_MEMORY", input_summary={"repo_id": repo_id},
                            output_summary=summary, status="ok", summary_limit=1500)
                out.append(summary)
            except Exception as exc:
                _trace_error(trace, run_id=run_id, repo_id=repo_id, exc=exc)
    return out
