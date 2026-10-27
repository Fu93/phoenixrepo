#!/usr/bin/env python3
"""Run one provenance-only Nightshift/Nemotron smoke check.

This is deliberately not an evaluation runner. It makes exactly one chat request,
writes a sealed trace, and prints the requested and actually served model. Run it
only in the isolated demo worktree, after other live work has stopped:

    python scripts/nvidia_live_smoke.py
    python scripts/verify_trace.py logs/trace-nvidia-live-smoke-<timestamp>.jsonl --require-nemotron

Configuration follows the real judge (scripts/revive.py, scripts/score_candidates.py):
config.settings.Settings supplies the base URL (METERLESS_BASE_URL), the runtime token
(METERLESS_RUNTIME_TOKEN) and, unless --model is given, the model (MODEL_NAME, then
METERLESS_MODEL, else DEFAULT_MODEL). RelevanceClient itself reads the PHOENIX_LLM_* body,
timeout and token-budget variables. No token is ever printed.

Every run uses its own fresh cache directory, <cache root>/<run id>, so the call can never
be answered from a cache. If the trace nevertheless shows cache_hit, the script exits non-zero.

A single live provenance smoke check requested <requested> and was served by
<served>. It verifies the strict Nemotron-family trace path only; it is not an
evaluation result, model-quality comparison, or Gate 2 repair claim.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.relevance import RelevanceClient, RelevanceLLMError  # noqa: E402
from app.console import prepare_cli  # noqa: E402
from app.trace_log import HackathonTrace  # noqa: E402
from config.settings import Settings, trace_secrets  # noqa: E402

DEFAULT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
SMOKE_PROMPT = 'Reply with exactly this JSON object: {"ok":true,"purpose":"provenance-smoke"}'
FIXED_CLAIM_TEMPLATE = (
    "A single live provenance smoke check requested {requested} and was served by {served}. It verifies the strict "
    "Nemotron-family trace path only; it is not an evaluation result, model-quality comparison, "
    "or Gate 2 repair claim."
)


EXIT_CACHE_HIT = 3
EXIT_NOT_CONFIGURED = 2


def _load_settings() -> Settings:
    """The same Settings object the real judge builds (reads the environment and .env)."""

    return Settings()


def _configured_model(settings: Settings) -> str | None:
    """MODEL_NAME or METERLESS_MODEL when one was explicitly set, else None (not the field default)."""

    if not ({"model_override", "meterless_model"} & set(settings.model_fields_set)):
        return None
    return settings.requested_model()


def _safe_name(run_id: str) -> str:
    """A single path segment made from the run id (letters, digits, dot, dash, underscore)."""

    name = re.sub(r"[^A-Za-z0-9._-]", "_", run_id).strip(".")
    return name or "run"


def _fresh_cache_dir(cache_root: Path, run_id: str) -> Path:
    """A new, empty cache directory for this run. Refuses to reuse a directory that has content."""

    directory = cache_root / _safe_name(run_id)
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError(f"cache directory {directory} already has content; use a new --run-id")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _trace_shows_cache_hit(trace_path: Path) -> bool:
    """True when any LLM_CALL line of the trace carries cache_hit in its output summary."""

    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("action") != "LLM_CALL":
            continue
        summary = record.get("output_summary")
        if isinstance(summary, str):
            try:
                summary = json.loads(summary)
            except ValueError:
                summary = {"cache_hit": True} if '"cache_hit"' in summary else {}
        if isinstance(summary, dict) and summary.get("cache_hit"):
            return True
    return False


def _run_id() -> str:
    return "nvidia-live-smoke-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _served_model(result: tuple[str, str, str]) -> str:
    """complete() returns (response_text, requested_model, served_model); an empty id is missing."""

    served = result[2] if len(result) > 2 else ""
    return served.strip() if isinstance(served, str) and served.strip() else "(missing)"


def main(argv: list[str] | None = None) -> int:
    prepare_cli()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--model",
        default=None,
        help="Requested model id; the served id is recorded without rewriting. Default: MODEL_NAME or METERLESS_MODEL when set (as for the real judge), else the Ultra DEFAULT_MODEL.",
    )
    parser.add_argument("--run-id", default="", help="Optional trace run id (default: UTC timestamp).")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "logs")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=ROOT / ".cache" / "nvidia-live-smoke",
        help="Root of the smoke caches. Each run gets a fresh <root>/<run id> directory; never reuses Gate 1 cache.",
    )
    args = parser.parse_args(argv)

    run_id = args.run_id or _run_id()
    try:
        settings = _load_settings()
    except Exception as exc:  # noqa: BLE001 - report the type only; the message could echo a setting
        print(f"Smoke not run: Settings could not be loaded ({type(exc).__name__}).", file=sys.stderr)
        return EXIT_NOT_CONFIGURED
    token = settings.meterless_runtime_token
    if not token or token == "rt-local":
        print("Smoke not run: METERLESS_RUNTIME_TOKEN is missing or invalid.", file=sys.stderr)
        return EXIT_NOT_CONFIGURED
    model = args.model or _configured_model(settings) or DEFAULT_MODEL
    try:
        cache_dir = _fresh_cache_dir(args.cache_dir, run_id)
    except (FileExistsError, OSError) as exc:
        print(f"Smoke not run: {exc}", file=sys.stderr)
        return EXIT_NOT_CONFIGURED

    trace = HackathonTrace(run_id, args.log_dir, trace_secrets(settings))
    client = RelevanceClient(
        base_url=settings.meterless_base_url,
        token=token,
        model=model,
        cache_dir=cache_dir,
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

    if client.last_cache_hit or _trace_shows_cache_hit(trace.path):
        run_hash = trace.seal()
        print(
            "SMOKE FAILED: the call was answered from a cache (cache_hit), so it was not a live call. No provenance claim is made.",
            file=sys.stderr,
        )
        print(f"Cache directory: {cache_dir}", file=sys.stderr)
        print(f"Trace: {trace.path}")
        print(f"Run hash: {run_hash}")
        return EXIT_CACHE_HIT

    served = _served_model(result)
    requested = result[1] if isinstance(result[1], str) and result[1].strip() else model
    run_hash = trace.seal()
    print(f"Requested: {requested}")
    print(f"Served: {served}")
    print(f"Cache: fresh per-run directory {cache_dir.name} (no cache hit)")
    print("Policy: require - accepted only when the served model remains in the NVIDIA Nemotron family.")
    print(f"Trace: {trace.path}")
    print(f"Run hash: {run_hash}")
    # The claim form is fixed, but ids are drawn from this trace: never call a routed
    # Nemotron-family answer an Ultra answer merely because Ultra was requested.
    print(FIXED_CLAIM_TEMPLATE.format(requested=requested, served=served))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
