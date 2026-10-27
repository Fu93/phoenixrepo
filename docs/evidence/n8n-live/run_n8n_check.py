#!/usr/bin/env python3
"""One live call through PhoenixRepo's gate-1 ToolBox to the n8n evidence webhook.

Run from the phoenixrepo root, with n8n running and the workflow
n8n/phoenix_evidence_webhook.json imported and active:

    set N8N_WEBHOOK_URL=http://localhost:5678/webhook/phoenix-evidence     (Windows cmd)
    $env:N8N_WEBHOOK_URL="http://localhost:5678/webhook/phoenix-evidence"  (PowerShell)
    python docs/evidence/n8n-live/run_n8n_check.py

It builds the same ToolBox gate 1 uses (scripts/score_candidates.build_default_toolbox),
makes one n8n call, writes a sealed hash-chained trace, and copies it next to this file.
It makes no model call and writes no verdict. Gate 1's search does not call n8n.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.trace_log import HackathonTrace  # noqa: E402
from scripts.score_candidates import build_default_toolbox  # noqa: E402

# The alternative page the scored SelfDB verdict quoted (data/replays/selfdb-io-selfdb).
URL = "https://github.com/Erudika/para"


def main() -> int:
    if not os.environ.get("N8N_WEBHOOK_URL"):
        print("N8N_WEBHOOK_URL is not set; nothing was called.")
        return 2
    here = Path(__file__).resolve().parent
    run_id = "n8n-live-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    trace = HackathonTrace(run_id, log_dir=ROOT / "logs")
    toolbox = build_default_toolbox(max_calls=3, trace=trace)
    if "n8n" not in toolbox.tools:
        print("The ToolBox did not register the n8n tool.")
        return 3
    res = toolbox.call("n8n", {"url": URL})
    run_hash = trace.seal()
    copy = here / f"trace-{run_id}.jsonl"
    shutil.copyfile(trace.path, copy)
    data = res.data if isinstance(res.data, (dict, list)) else str(res.data)[:300]
    summary = {
        "run_id": run_id,
        "run_hash": run_hash,
        "ok": res.ok,
        "error": res.error,
        "source_url": res.source_url,
        "requested_url": URL,
        "response_excerpt": json.dumps(data)[:400],
        "trace": copy.relative_to(ROOT).as_posix(),
    }
    (here / f"RESULT-{run_id}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nVerify: python scripts/verify_trace.py {summary['trace']} --expected-run-hash {run_hash}")
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
