"""Judge-facing JSONL trace. Fields are written by the running investigation, never by hand.

Each line carries prev_hash and line_hash. line_hash is sha256 of the canonical
JSON of the line including prev_hash and excluding line_hash. The chain shows
the file was not edited after the run. It does not show that the text is true.
"""

from __future__ import annotations

import hashlib
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


def canonical_body(record: dict[str, Any]) -> str:
    """Stable JSON for the hash. line_hash is not part of the hashed body."""

    body = {key: value for key, value in record.items() if key != "line_hash"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def canonical_line(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def line_hash(record: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_body(record).encode("utf-8")).hexdigest()


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
        self._prev_hash = ""
        self.run_hash = ""

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
        llm: dict[str, Any] | None = None,
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
        if llm is not None:
            # Top-level so a truncated input_summary cannot drop them, and so the hash covers them.
            record["requested_model"] = str(llm.get("requested_model") or "")
            record["served_model"] = llm.get("served_model")
        record = sanitize_metadata(record)
        record["prev_hash"] = self._prev_hash
        missing = [field for field in FIELDS if field not in record]
        if missing:
            raise RuntimeError(f"trace record missing {missing}")
        record["line_hash"] = line_hash(record)
        self._prev_hash = record["line_hash"]
        self.run_hash = record["line_hash"]
        line = canonical_line(record)
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

    def seal(self) -> str:
        """Append RUN_SEAL. Its line_hash is the run hash. The hash is not inside the hashed body."""

        record = self.event(
            agent_name="orchestrator",
            action="RUN_SEAL",
            input_summary={"prev_hash": self._prev_hash},
            output_summary="Final run hash is this line's line_hash.",
            status="ok",
        )
        return str(record["line_hash"])
