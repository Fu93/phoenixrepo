"""Judge-facing JSONL trace. Fields are written by the running investigation, never by hand."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from security.sanitizer import sanitize_metadata

FIELDS = (
    "timestamp",
    "run_id",
    "agent_name",
    "action",
    "input_summary",
    "output_summary",
    "target_agent",
    "confidence",
    "retry_count",
    "status",
)


def _redact(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {key: _redact(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secrets) for item in value]
    return value


def _summary(value: Any, secrets: list[str], limit: int = 500) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else json.dumps(_redact(value, secrets), ensure_ascii=False, default=str)
    text = _redact(text, secrets)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


class HackathonTrace:
    def __init__(self, run_id: str, log_dir: str | Path = "logs", secrets: list[str] | None = None) -> None:
        self.run_id = run_id
        self.secrets = [secret for secret in (secrets or []) if secret]
        directory = Path(log_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"trace-{run_id}.jsonl"
        self._lines: list[str] = []

    def event(
        self,
        *,
        agent_name: str,
        action: str,
        input_summary: Any = "",
        output_summary: Any = "",
        target_agent: str | None = None,
        confidence: float | None = None,
        retry_count: int = 0,
        status: str = "ok",
    ) -> dict[str, Any]:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "agent_name": agent_name,
            "action": action,
            "input_summary": _summary(input_summary, self.secrets),
            "output_summary": _summary(output_summary, self.secrets),
            "target_agent": target_agent,
            "confidence": confidence,
            "retry_count": retry_count,
            "status": status,
            "event": action,
        }
        record = sanitize_metadata(record)
        missing = [field for field in FIELDS if field not in record]
        if missing:
            raise RuntimeError(f"trace record missing {missing}")
        line = json.dumps(record, ensure_ascii=False)
        print(line, flush=True)
        self._lines.append(line)
        body = "\n".join(self._lines) + "\n"
        try:
            self.path.write_text(body, encoding="utf-8")
        except OSError:
            fallback = Path("/tmp/phoenixrepo-logs")
            fallback.mkdir(parents=True, exist_ok=True)
            self.path = fallback / f"trace-{self.run_id}.jsonl"
            self.path.write_text(body, encoding="utf-8")
        return record
