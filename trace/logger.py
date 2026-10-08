import json
from datetime import datetime, timezone
from pathlib import Path

from audit.events import AuditEvent
from security.sanitizer import sanitize_metadata

LOG_DIR = Path("run_artifacts/logs")


class TraceLogger:
    def __init__(self, run_id: str) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.path = LOG_DIR / f"trace-{run_id}.jsonl"
        self.lines: list[str] = []

    def event(self, event: str, **data) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event": event,
            "data": sanitize_metadata(data),
        }
        line = json.dumps(record, ensure_ascii=False)
        print(line, flush=True)
        self.lines.append(line)
        body = "\n".join(self.lines) + "\n"
        try:
            self.path.write_text(body, encoding="utf-8")
        except OSError:
            fallback = Path("/tmp/phoenixrepo-logs")
            fallback.mkdir(parents=True, exist_ok=True)
            self.path = fallback / f"trace-{self.run_id}.jsonl"
            self.path.write_text(body, encoding="utf-8")

    def audit_event(self, event: AuditEvent) -> None:
        self.event("AUDIT", **event.model_dump(mode="json"))
