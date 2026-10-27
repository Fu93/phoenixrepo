"""Gate-1 verdict memory on Meterless H-MEM.

Every gate-1 verdict is written to an H-MEM store (``app/engines/hmem``) as a long-term memory
with its run id, its reason and the served model. Before writing, the store is asked for the
verdicts it already holds for the same repository. The newest one is superseded by the new
verdict (H-MEM keeps it, it does not delete), and when the two differ the trace line says so.

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


def _current(records: list[Any]) -> list[Any]:
    """Verdict memories not yet superseded, newest first."""
    live = [r for r in records if not r.superseded_by]
    return sorted(live, key=lambda r: r.timestamp, reverse=True)


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
    """
    if os.environ.get("PHOENIX_HMEM", "").strip().lower() in {"off", "0", "false", "no"}:
        return []
    try:
        store = _store(log_dir)
    except Exception as exc:  # H-MEM must never stop gate 1
        trace.event(agent_name="hmem", action="HMEM_VERDICT_MEMORY", input_summary={"run_id": run_id},
                    output_summary={"error": f"{type(exc).__name__}: {str(exc)[:200]}"}, status="error")
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        repo_id = str(row.get("repo_id") or "").strip()
        verdict = str(row.get("verdict") or "").strip()
        if not repo_id or not verdict:
            continue
        try:
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
                extra={"kind": "verdict", "verdict": verdict, "served_model": (served_by_repo or {}).get(repo_id, "")},
            )
            if prior:
                store.supersede(prior[0].id, record.id)
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
            trace.event(agent_name="hmem", action="HMEM_VERDICT_MEMORY", input_summary={"repo_id": repo_id},
                        output_summary={"error": f"{type(exc).__name__}: {str(exc)[:200]}"}, status="error")
    return out
