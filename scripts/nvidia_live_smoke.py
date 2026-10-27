#!/usr/bin/env python3
"""Run one provenance-only Nightshift/Nemotron smoke check.

This is deliberately not an evaluation runner. It makes exactly one chat request,
writes a sealed trace, and prints the requested and actually served model. Run it
only in the isolated demo worktree, after other live work has stopped:

    python scripts/nvidia_live_smoke.py
    python scripts/verify_trace.py logs/trace-nvidia-live-smoke-<timestamp>.jsonl --require-nemotron

A single live provenance smoke check requested <requested> and was served by
<served>. It verifies the strict Nemotron-family trace path only; it is not an
evaluation result, model-quality comparison, or Gate 2 repair claim.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.relevance import RelevanceClient, RelevanceLLMError  # noqa: E402
from app.console import prepare_cli  # noqa: E402
from app.trace_log import HackathonTrace  # noqa: E402
from config.settings import trace_secrets  # noqa: E402

DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
SMOKE_PROMPT = 'Reply with exactly this JSON object: {"ok":true,"purpose":"provenance-smoke"}'
FIXED_CLAIM_TEMPLATE = (
    "A single live provenance smoke check requested {requested} and was served by {served}. It verifies the strict "
    "Nemotron-family trace path only; it is not an evaluation result, model-quality comparison, "
    "or Gate 2 repair claim."
)


def _run_id() -> str:
    return "nvidia-live-smoke-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _served_model(result: tuple[str, str, str]) -> str:
    """complete() returns (response_text, requested_model, served_model); an empty id is missing."""

    served = result[2] if len(result) > 2 else ""
    return served.strip() if isinstance(served, str) and served.strip() else "(missing)"


def main(argv: list[str] | None = None) -> int:
    prepare_cli()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Requested model id; the served id is recorded without rewriting.")
    parser.add_argument("--run-id", default="", help="Optional trace run id (default: UTC timestamp).")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "logs")
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".cache" / "nvidia-live-smoke", help="Dedicated smoke cache; never reuses Gate 1 cache.")
    args = parser.parse_args(argv)

    run_id = args.run_id or _run_id()
    trace = HackathonTrace(run_id, args.log_dir, trace_secrets())
    client = RelevanceClient(
        model=args.model,
        cache_dir=args.cache_dir,
        trace=trace,
        nemotron_policy="require",
    )
    try:
        result = client.complete(SMOKE_PROMPT, repo_id="nvidia/live-smoke", step="discovery")
    except RelevanceLLMError as exc:
        run_hash = trace.seal()
        print(f"Smoke request failed: {exc}", file=sys.stderr)
        print(f"Trace: {trace.path}")
        print(f"Run hash: {run_hash}")
        return 1

    served = _served_model(result)
    requested = result[1] if isinstance(result[1], str) and result[1].strip() else args.model
    run_hash = trace.seal()
    print(f"Requested: {requested}")
    print(f"Served: {served}")
    print("Policy: require - accepted only when the served model remains in the NVIDIA Nemotron family.")
    print(f"Trace: {trace.path}")
    print(f"Run hash: {run_hash}")
    # The claim form is fixed, but ids are drawn from this trace: never call a routed
    # Nemotron-family answer an Ultra answer merely because Ultra was requested.
    print(FIXED_CLAIM_TEMPLATE.format(requested=requested, served=served))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
