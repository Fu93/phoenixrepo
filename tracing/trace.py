import json
from datetime import datetime, timezone
from pathlib import Path


def write_trace(log_dir: Path, run_id: str, event: dict) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"trace-{run_id}.jsonl"
    row = {"timestamp": datetime.now(timezone.utc).isoformat(), "run_id": run_id, **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    print(
        f"[INFO] run_id={run_id} {event.get('agent_name', 'system')} {event.get('action', 'event')}",
        flush=True,
    )
    return path
