#!/usr/bin/env python3
"""Read-only check that gate 1 can load its evidence from Zetaris. No model call.

    python scripts/zetaris_check.py                      # every repo_id in data/real
    python scripts/zetaris_check.py --snapshot data/real --repo jondot/hygen

Sends only the single-table SELECT statements from app/sql_catalog.py (the preflight, then
discovery_candidates, prior_art and demand_signals per repository) through the REST client,
prints row counts with each query_id and content_hash, and writes a sealed trace that
scripts/verify_trace.py can check. The API key is never printed. Exit 0 when every
repository returned its candidate row, 1 when some did not, 2 when Zetaris did not answer.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.adapters.zetaris_evidence import (  # noqa: E402
    ZetarisEvidenceSource,
    ZetarisPreflightError,
    ZetarisUnavailable,
    build_live_client,
    cohort_repo_ids,
    preflight,
)
try:  # app.console arrives with the Nightshift PR; either merge order works.
    from app.console import prepare_cli  # noqa: E402
except ImportError:  # pragma: no cover
    def prepare_cli() -> None:
        return None
from app.trace_log import HackathonTrace  # noqa: E402
from config.settings import trace_secrets  # noqa: E402


def main(argv: list[str] | None = None, *, client=None) -> int:
    prepare_cli()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--snapshot", type=Path, default=ROOT / "data" / "real", help="Snapshot whose discovery_candidates.csv names the repo ids.")
    ap.add_argument("--repo", action="append", default=[], help="Only these repo ids (repeatable).")
    ap.add_argument("--log-dir", type=Path, default=ROOT / "logs")
    args = ap.parse_args(argv)

    repo_ids = args.repo or cohort_repo_ids(args.snapshot)
    if not repo_ids:
        sys.stderr.write(f"No repo ids: {args.snapshot / 'discovery_candidates.csv'} is missing or empty, and no --repo was given.\n")
        return 2
    run_id = "zetaris-check-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    trace = HackathonTrace(run_id, args.log_dir, trace_secrets())
    try:
        client = client or build_live_client()
        record = preflight(client, repo_ids)
    except (ZetarisPreflightError, Exception) as exc:  # noqa: BLE001
        trace.event(agent_name="gate1", action="ZETARIS_PREFLIGHT", output_summary=str(exc), status="failed")
        trace.seal()
        sys.stderr.write(f"{exc}\n")
        return 2
    trace.event(agent_name="gate1", action="zetaris_query", input_summary={"sql": record.sql}, output_summary=vars(record), status="preflight")
    print(f"Zetaris answered at {getattr(client, 'rest_url', '')}: preflight query_id={record.query_id} rows={record.row_count}")
    missing = 0
    source = ZetarisEvidenceSource(client)
    for repo_id in repo_ids:
        try:
            bundle = source.fetch(repo_id)
        except ZetarisUnavailable as exc:
            missing += 1
            print(f"{repo_id:<45} unreachable ({exc})")
            for query in exc.queries:
                trace.event(agent_name="gate1", action="zetaris_query", input_summary={"repo_id": repo_id, "sql": query.sql}, output_summary=vars(query), status=query.status)
            continue
        for query in bundle.queries:
            trace.event(agent_name="gate1", action="zetaris_query", input_summary={"repo_id": repo_id, "sql": query.sql}, output_summary=vars(query), status=query.status)
        counts = " ".join(f"{q.view.split('.')[-1]}={q.row_count}" for q in bundle.queries)
        hashes = " ".join(q.content_hash[:12] for q in bundle.queries)
        state = "ok" if bundle.candidate else "NO CANDIDATE ROW"
        if not bundle.candidate:
            missing += 1
        print(f"{repo_id:<45} {state:<16} {counts}  hashes {hashes}")
    run_hash = trace.seal()
    print(f"Trace: {trace.path}  run hash {run_hash}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
