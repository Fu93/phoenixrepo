"""Connection test for Meterless Nightshift. Does not print the runtime token.

Usage (from the repo root, with .env or the variables exported):

  python scripts/test_meterless_connection.py
  python scripts/test_meterless_connection.py --auto-detect

Exit 0 when discovery is online and a one-line chat returns text.
Unauthorized is exit 2. Offline is exit 3. /healthz is not consulted.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from app.adapters.meterless import MeterlessRuntime  # noqa: E402
from config.settings import Settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Test the Meterless Runtime connection.")
    parser.add_argument("--auto-detect", action="store_true", help="Probe the documented local ports.")
    args = parser.parse_args()
    settings = Settings()
    requested = settings.requested_model()
    runtime = MeterlessRuntime(
        base_url=settings.meterless_base_url,
        token=settings.meterless_runtime_token,
        model=requested,
        app_id=settings.meterless_app,
        timeout_seconds=settings.meterless_timeout_seconds,
        max_tokens=settings.meterless_max_tokens,
    )
    try:
        status = runtime.auto_detect() if args.auto_detect else runtime.connection_status()
    except Exception as exc:
        print(f"FAIL {exc.__class__.__name__}: {exc}")
        return 1
    print(f"discovery {status.state} base={status.base_url} models={','.join(status.models) or '-'}")
    print(status.detail)
    if status.state == "unauthorized":
        return 2
    if status.state != "online":
        return 3
    runtime.base_url = status.base_url
    try:
        reply = runtime.complete(
            [{"role": "user", "content": "Reply with the single word: ready"}],
            model=requested,
        )
    except Exception as exc:
        print(f"FAIL chat: {exc}")
        return 1
    print(
        f"chat ok requested={runtime.last_requested_model} "
        f"served={runtime.last_served_model or '-'} reply={reply.strip()[:120]}"
    )
    return 0


if __name__ == "__main__":
    from app.console import prepare_cli

    prepare_cli()
    raise SystemExit(main())
