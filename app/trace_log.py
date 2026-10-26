"""Judge-facing JSONL trace. Fields are written by the running investigation, never by hand.

Each line carries prev_hash and line_hash. line_hash is sha256 of the canonical
JSON of the line including prev_hash and excluding line_hash. The chain shows
the file was not edited after the run. It does not show that the text is true.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config.settings import model_mismatch
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


# Credential shapes that can surface in command output even when the value is not in `secrets`
# (a GitHub token printed by a test, an NVIDIA key in an error body).
_TOKEN_PATTERNS = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|nvapi-[A-Za-z0-9_-]{20,}|sk-ant-[A-Za-z0-9_-]{20,}|xai-[A-Za-z0-9]{20,})\b"
)


def _redact(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[REDACTED]")
        return _TOKEN_PATTERNS.sub("[REDACTED]", value)
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
        self._started = False

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
        summary_limit: int = 500,
    ) -> dict[str, Any]:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "agent_name": agent_name,
            "action": action,
            "input_summary": _summary(input_summary, self.secrets),
            "output_summary": _summary(output_summary, self.secrets, limit=summary_limit),
            "target_agent": target_agent,
            "confidence": confidence,
            "retry_count": retry_count,
            "status": status,
            "event": action,
        }
        if llm is not None:
            # Top-level so a truncated input_summary cannot drop them, and so the hash covers them.
            record["requested_model"] = str(llm.get("requested_model") or "")
            served = llm.get("served_model")
            record["served_model"] = served
            if model_mismatch(served):
                # Visible on the line. Do not replace served_model with the requested id.
                record["flags"] = ["MODEL_MISMATCH"]
            if llm.get("prompt_version"):
                record["prompt_version"] = str(llm["prompt_version"])
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
        self._write(line)
        return record

    def model_mismatch_count(self) -> int:
        """How many LLM lines in this run were flagged MODEL_MISMATCH."""

        count = 0
        for line in self._lines:
            try:
                flags = json.loads(line).get("flags") or []
            except json.JSONDecodeError:
                continue
            if "MODEL_MISMATCH" in flags:
                count += 1
        return count

    def _write(self, line: str) -> None:
        """Truncate on the first line of this run, append after that.

        Rewriting the whole file on every event was O(n^2) for long revive and batch traces.
        A rerun with the same run_id still starts a fresh file, as before.
        """
        try:
            if self._started:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            else:
                self.path.write_text(line + "\n", encoding="utf-8")
                self._started = True
        except OSError:
            fallback = Path(tempfile.gettempdir()) / "phoenixrepo-logs"
            fallback.mkdir(parents=True, exist_ok=True)
            self.path = fallback / f"trace-{self.run_id}.jsonl"
            self.path.write_text("\n".join(self._lines) + "\n", encoding="utf-8")
            self._started = True

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
