import json
from datetime import datetime, timezone
from pathlib import Path

LOG_DIR = Path("logs")


class TraceLogger:
    def __init__(self, run_id: str) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.path = LOG_DIR / f"trace-{run_id}.jsonl"

    def event(self, event: str, **data) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event": event,
            "data": data,
        }
        line = json.dumps(record, ensure_ascii=False)
        print(line, flush=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
