"""Shared toolbox base protocol, result dataclass, and budget tracker."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from app.trace_log import HackathonTrace
from security.sanitizer import sanitize_metadata


@dataclass
class ToolResult:
    """Standardized tool execution result."""

    ok: bool
    data: Any
    error: str
    source_url: str
    fetched_at: str
    sha256: str = ""
    evidence_id: str = ""

    def __post_init__(self) -> None:
        if not self.sha256:
            if isinstance(self.data, str):
                content = self.data
            else:
                content = json.dumps(self.data, sort_keys=True, separators=(",", ":"), default=str)
            self.sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if not self.evidence_id:
            self.evidence_id = f"tool:{self.sha256[:16]}"

    def to_evidence_item(self, repo_id: str = "") -> dict[str, Any]:
        """Convert result into an evidence item compatible with the evidence index."""
        if isinstance(self.data, str):
            text = self.data
        else:
            text = json.dumps(self.data, ensure_ascii=False, default=str)
        return {
            "id": self.evidence_id,
            "url": self.source_url,
            "text": text,
            "content_hash": self.sha256,
            "fetched_at": self.fetched_at,
            "repo_id": repo_id,
        }


class Tool(Protocol):
    """Protocol satisfied by all shared toolbox tools."""

    name: str

    def call(self, args: dict[str, Any]) -> ToolResult:
        ...


@dataclass
class Budget:
    """Execution budget for tool invocations."""

    max_calls: int = 20
    max_seconds: float = 60.0
    calls_used: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def remaining_calls(self) -> int:
        return max(0, self.max_calls - self.calls_used)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.max_seconds - self.elapsed_seconds)

    def is_exhausted(self) -> bool:
        return self.calls_used >= self.max_calls or self.elapsed_seconds >= self.max_seconds

    def consume(self) -> bool:
        if self.is_exhausted():
            return False
        self.calls_used += 1
        return True

    def carve_child(self, max_calls: int = 5, max_seconds: float | None = None) -> ChildBudget:
        sec = min(self.remaining_seconds, max_seconds if max_seconds is not None else self.remaining_seconds)
        calls = min(self.remaining_calls, max_calls)
        return ChildBudget(parent=self, max_calls=calls, max_seconds=sec)


@dataclass
class ChildBudget:
    """A sub-agent budget carved from a parent Budget."""

    parent: Budget | ChildBudget
    max_calls: int = 5
    max_seconds: float = 60.0
    calls_used: int = 0
    started_at: float = field(default_factory=time.monotonic)

    @property
    def remaining_calls(self) -> int:
        local_rem = max(0, self.max_calls - self.calls_used)
        return min(local_rem, self.parent.remaining_calls)

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    @property
    def remaining_seconds(self) -> float:
        local_rem = max(0.0, self.max_seconds - self.elapsed_seconds)
        return min(local_rem, self.parent.remaining_seconds)

    def is_exhausted(self) -> bool:
        return (
            self.calls_used >= self.max_calls
            or self.elapsed_seconds >= self.max_seconds
            or self.parent.is_exhausted()
        )

    def consume(self) -> bool:
        if self.is_exhausted():
            return False
        if not self.parent.consume():
            return False
        self.calls_used += 1
        return True

    def carve_child(self, max_calls: int = 5, max_seconds: float | None = None) -> ChildBudget:
        sec = min(self.remaining_seconds, max_seconds if max_seconds is not None else self.remaining_seconds)
        calls = min(self.remaining_calls, max_calls)
        return ChildBudget(parent=self, max_calls=calls, max_seconds=sec)


class ToolBox:
    """Registry and hash-chained execution manager for shared tools."""

    def __init__(
        self,
        tools: dict[str, Tool] | None = None,
        budget: Budget | ChildBudget | None = None,
        trace: HackathonTrace | None = None,
    ) -> None:
        self.tools = dict(tools or {})
        self.budget = budget if budget is not None else Budget()
        self.trace = trace

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def call(self, name: str, args: dict[str, Any] | None = None) -> ToolResult:
        """Invoke a tool by name with budget checking and hash-chained trace logging."""
        args_dict = dict(args or {})
        tool = self.tools.get(name)
        redacted_args = self._redact_args(args_dict)
        now = datetime.now(timezone.utc).isoformat()

        if not self.budget.consume():
            res = ToolResult(
                ok=False,
                data="",
                error="BUDGET_EXHAUSTED",
                source_url="",
                fetched_at=now,
            )
            self._log_trace(name, redacted_args, res)
            return res

        if tool is None:
            res = ToolResult(
                ok=False,
                data="",
                error=f"Unknown tool '{name}'",
                source_url="",
                fetched_at=now,
            )
            self._log_trace(name, redacted_args, res)
            return res

        try:
            res = tool.call(args_dict)
        except Exception as exc:
            res = ToolResult(
                ok=False,
                data="",
                error=f"Tool error: {exc}",
                source_url="",
                fetched_at=now,
            )

        self._log_trace(name, redacted_args, res)
        return res

    def _redact_args(self, args: dict[str, Any]) -> dict[str, Any]:
        """Redact any sensitive headers, tokens, or environment values."""
        return sanitize_metadata(args)

    def _log_trace(self, tool_name: str, args: dict[str, Any], result: ToolResult) -> None:
        if self.trace is not None:
            self.trace.event(
                agent_name="toolbox",
                action="TOOL_CALL",
                input_summary={"tool": tool_name, "args": args},
                output_summary={
                    "ok": result.ok,
                    "error": result.error,
                    "source_url": result.source_url,
                    "sha256": result.sha256,
                },
                status="ok" if result.ok else (result.error or "error"),
            )

    def child_box(
        self,
        allowed_tools: Collection[str],
        max_calls: int = 5,
        max_seconds: float | None = None,
    ) -> ToolBox:
        """Carve a child toolbox with a child budget and allow-listed tools."""
        child_budget = self.budget.carve_child(max_calls=max_calls, max_seconds=max_seconds)
        sub_tools = {k: v for k, v in self.tools.items() if k in allowed_tools}
        return ToolBox(tools=sub_tools, budget=child_budget, trace=self.trace)
