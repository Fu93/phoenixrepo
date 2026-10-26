"""Semantic relevance extraction and judgment via Nightshift LLM.

Follows the pattern of app/agents/equivalence.py: the LLM judges meaning,
deterministic code strictly verifies every quote and invariant against stored evidence.

Functions:
- extract_capabilities: extracts up to 5 user-facing capabilities with exact README quotes.
- judge_demand: evaluates if an external issue/discussion expresses real demand for a capability.
- judge_alternative_coverage: evaluates if a maintained free alternative covers a capability.
- judge_discovery: evaluates whether a candidate repo is CANDIDATE, ALREADY_EXISTS, or NO_GO.
- extract_problem_statement: extracts problem statement, target user, and search queries (step="problem").
- search_prior_art: searches GitHub repositories (and npm/PyPI) via toolbox.
- judge_alternative_relevance: evaluates alternative relevance and overlap (step="alternative").
- filter_relevant_alternatives: filters search results to same_problem=true with verified quotes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from app.errors import InvestigationError
from app.prompts import load_prompt
from config.settings import DEFAULT_NIGHTSHIFT_MODEL, model_mismatch

ROOT = Path(__file__).resolve().parents[2]

GENERIC_WORDS = frozenset(
    {
        "code",
        "config",
        "https",
        "http",
        "python",
        "main",
        "install",
        "source",
    }
)

STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "to",
        "for",
        "and",
        "or",
        "in",
        "on",
        "with",
        "of",
        "is",
        "it",
        "by",
        "as",
        "at",
        "from",
        "this",
        "that",
    }
)

RETRY_DELAYS = (0, 3, 8, 20, 45)

# Defaults of the Nightshift client. Each can be changed per machine without code edits:
#   PHOENIX_LLM_ATTEMPT_TIMEOUT_S  one HTTP attempt (read timeout and wall clock), default 120
#   PHOENIX_LLM_TOTAL_TIMEOUT_S    one call across retries, default 420
#   PHOENIX_LLM_MAX_TOKENS         completion budget, default 4096
#   PHOENIX_LLM_EXTRA_BODY         JSON object merged into the request body, for example
#                                  {"chat_template_kwargs": {"enable_thinking": false}}
# scripts/nightshift_probe.py measures which values work on this machine's Nightshift.
DEFAULT_ATTEMPT_TIMEOUT_S = 120.0
DEFAULT_READ_TIMEOUT_S = 90.0
DEFAULT_TOTAL_TIMEOUT_S = 420.0
DEFAULT_MAX_TOKENS = 4096
_PROTECTED_BODY_KEYS = frozenset({"model", "messages", "stream"})


def _env_positive(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def extra_body_from_env(name: str = "PHOENIX_LLM_EXTRA_BODY") -> dict[str, Any]:
    """A JSON-object env var as a dict. Invalid JSON or a non-object is ignored with a warning."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        sys.stderr.write(f"warning: {name} is not valid JSON; ignored\n")
        return {}
    if not isinstance(value, dict):
        sys.stderr.write(f"warning: {name} must be a JSON object; ignored\n")
        return {}
    return {key: item for key, item in value.items() if key not in _PROTECTED_BODY_KEYS}


# Two Nemotron tiers. Many short overlap judgements go to the fast model (MODEL_NAME, usually
# a small Nemotron with thinking off). The steps in PHOENIX_DEEP_STEPS (default: the final
# discovery verdict) go to PHOENIX_DEEP_MODEL with its own body, so a larger Nemotron can reason
# where the verdict is made. Unset PHOENIX_DEEP_MODEL keeps one model for every step.
DEFAULT_DEEP_STEPS = ("discovery",)
NEMOTRON_POLICIES = ("label", "require", "fallback-nvidia")
DEFAULT_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
REASONING_EXCERPT_CHARS = 1200
_THINK_RE = re.compile(r"<think>(.*?)</think>", re.S | re.I)


def _env_text(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def nemotron_policy_from_env() -> str:
    """PHOENIX_NEMOTRON_POLICY: label (default), require, or fallback-nvidia.

    label            a non-Nemotron answer is used and flagged MODEL_MISMATCH (honest default)
    require          a non-Nemotron answer is discarded and the attempt is retried through Nightshift
    fallback-nvidia  a non-Nemotron answer, a timeout or a 5xx is replaced by the same Nemotron model
                     called directly at the NVIDIA API (NVIDIA_API_KEY); the trace says nvidia-direct
    """
    value = os.environ.get("PHOENIX_NEMOTRON_POLICY", "").strip().lower() or "label"
    if value not in NEMOTRON_POLICIES:
        sys.stderr.write(f"warning: PHOENIX_NEMOTRON_POLICY={value!r} is not one of {', '.join(NEMOTRON_POLICIES)}; using label\n")
        return "label"
    return value


def reasoning_excerpt(message: Any, text: str) -> str:
    """The model's own reasoning when the reply carries it: a reasoning field or a <think> block.

    Kept short (head and tail) for the trace. It is shown as the model's account; the verdict
    still stands or falls on the verbatim quote checks, not on this text.
    """
    raw = ""
    if isinstance(message, dict):
        for key in ("reasoning_content", "reasoning"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                raw = value
                break
    if not raw and text:
        match = _THINK_RE.search(text)
        if match:
            raw = match.group(1)
    raw = raw.strip()
    if len(raw) <= REASONING_EXCERPT_CHARS:
        return raw
    half = REASONING_EXCERPT_CHARS // 2
    return raw[:half].rstrip() + "\n...\n" + raw[-half:].lstrip()


def _message_text(data: Any) -> tuple[Any, str]:
    choices = data.get("choices") if isinstance(data, dict) else None
    if not (isinstance(choices, list) and choices and isinstance(choices[0], dict)):
        return {}, ""
    msg = choices[0].get("message") or {}
    content = msg.get("content") or "" if isinstance(msg, dict) else ""
    if isinstance(content, list):
        return msg, "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return msg, str(content)


def routing_meta(data: Any, headers: Any = None) -> dict[str, str]:
    """What Nightshift says about its own routing, when it says anything.

    Some Nightshift builds return a `meterless` block in the body (routed_model, winning_route)
    and some send `meterless-*` response headers. Both are optional; an empty dict means the
    response carried no routing detail, and the response `model` field stays the served model.
    """
    meta: dict[str, str] = {}
    block = data.get("meterless") if isinstance(data, dict) else None
    if isinstance(block, dict):
        for key in ("routed_model", "requested_model", "route", "lane", "mode"):
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                meta[key] = value.strip()[:120]
        route = block.get("winning_route")
        if isinstance(route, dict):
            for key in ("provider", "model"):
                value = route.get(key)
                if isinstance(value, str) and value.strip():
                    meta[f"winning_{key}"] = value.strip()[:120]
    if headers is not None:
        try:
            items = list(headers.items())
        except AttributeError:
            items = []
        for key, value in items:
            name = str(key).lower()
            if name.startswith("meterless-") and isinstance(value, str) and value.strip():
                meta[name] = value.strip()[:120]
    return meta


class RelevanceLLMError(InvestigationError):
    def __init__(self, message: str, *, status_code: int = 502) -> None:
        super().__init__("RelevanceLLMError", message, status_code)


class InfraFallbackStop(Exception):
    """The alternatives phase stopped for infrastructure, with any rows already verified.

    ``cause`` is ``time_cap`` or ``infra_failures``. ``verified`` is the same-problem rows
    whose quotes already checked out. ``unjudged_chunks`` is how many chunks never finished.
    The caller applies the maturity rule to ``verified``; this exception is not itself a verdict.
    """

    def __init__(
        self,
        message: str,
        *,
        cause: str,
        completed_calls: int = 0,
        batch_502s: int = 0,
        per_item_502s: int = 0,
        failures: int = 0,
    ) -> None:
        super().__init__(message)
        self.cause = cause
        self.completed_calls = completed_calls
        self.batch_502s = batch_502s
        self.per_item_502s = per_item_502s
        self.failures = failures
        self.verified: list[dict[str, Any]] = []
        self.unjudged_chunks: int = 0
        self.verified_chunk_indexes: list[int] = []

    def attach(
        self,
        verified: list[dict[str, Any]],
        unjudged_chunks: int,
        verified_chunk_indexes: list[int] | None = None,
    ) -> None:
        """Keep every verified row. A later attach adds rows this one does not already hold."""
        have = {str(row.get("alternative_id") or row.get("url") or "").casefold() for row in self.verified}
        for row in verified:
            key = str(row.get("alternative_id") or row.get("url") or "").casefold()
            if key and key in have:
                continue
            self.verified.append(row)
            if key:
                have.add(key)
        self.unjudged_chunks = unjudged_chunks
        if verified_chunk_indexes is not None:
            merged = list(self.verified_chunk_indexes)
            for index in verified_chunk_indexes:
                if index not in merged:
                    merged.append(index)
            self.verified_chunk_indexes = merged


# One repository must not spend an hour re-judging every alternative after HTTP 502.
# This bounds failed batch retries and per-item infrastructure time. A call that returns
# a judgement does not consume it. The filter replaces this floor with
# alternatives_budget_seconds(chunk_count): max(floor, per-chunk seconds × chunks),
# still capped, and still charged only on failures.
FALLBACK_BUDGET_S = 600.0
FALLBACK_INFRA_LIMIT = 2
ALTERNATIVES_BUDGET_FLOOR_S = 600.0
ALTERNATIVES_CHUNK_SECONDS = 90.0
ALTERNATIVES_BUDGET_CAP_S = 3600.0
_INFRA_ERROR_TOKENS = ("502", "503", "504", "429", "timeout", "timed out", "http 5")


def _infra_error(message: str) -> bool:
    low = str(message or "").lower()
    return any(token in low for token in _INFRA_ERROR_TOKENS)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def alternatives_budget_seconds(chunk_count: int) -> float:
    """Per-repo seconds for alternative judging. Only failures spend the clock.

    The formula is max(floor, per-chunk seconds × chunks), then the overall cap.
    ``PHOENIX_ALTERNATIVES_BUDGET_S`` replaces the formula. ``PHOENIX_ALTERNATIVES_BUDGET_FLOOR_S``
    (default 600), ``PHOENIX_ALTERNATIVES_CHUNK_SECONDS`` (default 90), and
    ``PHOENIX_ALTERNATIVES_BUDGET_CAP_S`` (default 3600) adjust it. One Nightshift attempt is
    still bounded by the client's total timeout.
    """
    cap = _env_float("PHOENIX_ALTERNATIVES_BUDGET_CAP_S", ALTERNATIVES_BUDGET_CAP_S)
    override = os.environ.get("PHOENIX_ALTERNATIVES_BUDGET_S", "").strip()
    if override:
        try:
            value = max(0.0, float(override))
        except ValueError:
            value = 0.0
    else:
        floor = _env_float("PHOENIX_ALTERNATIVES_BUDGET_FLOOR_S", ALTERNATIVES_BUDGET_FLOOR_S)
        per_chunk = _env_float("PHOENIX_ALTERNATIVES_CHUNK_SECONDS", ALTERNATIVES_CHUNK_SECONDS)
        value = max(floor, per_chunk * max(0, int(chunk_count)))
    if cap > 0:
        value = min(cap, value)
    return value


class FallbackBudget:
    """Per-repo budget for alternative judging after infrastructure failures.

    Successful model calls increment ``completed_calls`` and do not move the clock.
    HTTP 502 on a batch call increments ``batch_502s``. The same on a per-item call
    increments ``per_item_502s``. Time is charged only while a call is failing, and
    every batch attempt receives only the time still left, so two retries cannot run
    for the client's full timeout each.
    """

    def __init__(self, seconds: float = FALLBACK_BUDGET_S, clock: Callable[[], float] | None = None) -> None:
        self.seconds = seconds
        self.clock = clock or time.monotonic
        self.failures = 0
        self.batch_502s = 0
        self.per_item_502s = 0
        self.completed_calls = 0
        self.infra_seconds = 0.0
        self.last_cause = ""

    def remaining(self) -> float:
        return self.seconds - self.infra_seconds

    def time_exhausted(self) -> bool:
        if self.infra_seconds <= 0:
            return False
        return self.infra_seconds >= self.seconds or self.remaining() <= 1.0

    def stop(self, cause: str) -> InfraFallbackStop:
        """Build the stop from the trigger that actually fired."""
        if cause == "time_cap":
            message = (
                f"infra: time cap exceeded after {self.completed_calls} completed alternative calls "
                f"with {self.batch_502s} batch 502s and {self.per_item_502s} per-item 502s"
            )
        else:
            message = f"infra: stopped after {self.failures} infrastructure failures"
        if self.last_cause:
            message = f"{message}; cause: {self.last_cause}"
        return InfraFallbackStop(
            message,
            cause=cause,
            completed_calls=self.completed_calls,
            batch_502s=self.batch_502s,
            per_item_502s=self.per_item_502s,
            failures=self.failures,
        )

    def guard_new_chunk(self) -> None:
        """Stop before another chunk. The chunk whose batch just failed may still fall back per item."""
        if self.failures >= FALLBACK_INFRA_LIMIT:
            raise self.stop("infra_failures")
        if self.time_exhausted():
            raise self.stop("time_cap")

    def allow(self) -> float:
        """Seconds a per-item salvage call may spend. Failure count does not block that salvage."""
        if self.time_exhausted():
            raise self.stop("time_cap")
        return max(0.0, self.remaining())

    def note_completed(self) -> None:
        self.completed_calls += 1

    def charge_failure(self, elapsed: float, message: str, *, kind: str) -> None:
        """Count one infrastructure failure. ``elapsed`` is the time of this failed call only."""
        self.infra_seconds += max(0.0, float(elapsed))
        text = str(message or "").strip()
        if text:
            self.last_cause = text[:180]
        if not _infra_error(text):
            return
        self.failures += 1
        if "502" in text:
            if kind == "batch":
                self.batch_502s += 1
            else:
                self.per_item_502s += 1

    def note(self, message: str) -> None:
        """Compatibility for callers that only know an error string."""
        self.charge_failure(0.0, message, kind="per_item")


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Robustly extract a JSON object from text or markdown code fences."""
    raw = text.strip()
    if "```" in raw:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw)
        if match:
            raw = match.group(1).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(raw[start : end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return None


def _matching_brace(raw: str, start: int) -> int | None:
    """Index of the `}` that closes the object starting at `start`, or None if it was cut off."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def extract_result_entries(text: str) -> list[dict[str, Any]]:
    """Complete objects from a batch `results` array.

    A Nightshift reply often starts with a reasoning preamble and is cut at `max_tokens`
    (4096 on this client) before the array closes. `extract_json_object` then fails on the
    whole reply. Objects that already closed are still usable, so they are kept and the
    caller retries only the alternatives that were not in the prefix.
    """
    obj = extract_json_object(text)
    if isinstance(obj, dict) and isinstance(obj.get("results"), list):
        return [e for e in obj["results"] if isinstance(e, dict)]
    raw = str(text or "")
    idx = raw.find('"results"')
    if idx == -1:
        return []
    bracket = raw.find("[", idx)
    if bracket == -1:
        return []
    entries: list[dict[str, Any]] = []
    i = bracket + 1
    n = len(raw)
    while i < n:
        while i < n and raw[i] in " \t\r\n,":
            i += 1
        if i >= n or raw[i] == "]":
            break
        if raw[i] != "{":
            break
        end = _matching_brace(raw, i)
        if end is None:
            break
        try:
            parsed = json.loads(raw[i : end + 1])
        except json.JSONDecodeError:
            break
        if isinstance(parsed, dict):
            entries.append(parsed)
        i = end + 1
    return entries


def _complete_json(client, prompt, system_prompt, repo_id, step, validate=None):
    """Use client.complete_json when available (retries unusable answers), else one plain call."""
    fn = getattr(client, "complete_json", None)
    if callable(fn):
        return fn(prompt, system_prompt, repo_id=repo_id, step=step, validate=validate)
    text, req_m, srv_m = client.complete(prompt, system_prompt, repo_id=repo_id, step=step)
    obj = extract_json_object(text)
    if obj is not None and validate is not None and not validate(obj):
        obj = None
    return obj, text, req_m, srv_m


@dataclass
class RelevanceClient:
    base_url: str = "http://127.0.0.1:8088/v1"
    token: str | None = None
    model: str = DEFAULT_NIGHTSHIFT_MODEL
    cache_dir: Path | None = None
    timeout: float | httpx.Timeout = DEFAULT_READ_TIMEOUT_S
    wall_clock_timeout: float = DEFAULT_ATTEMPT_TIMEOUT_S
    total_timeout: float = DEFAULT_TOTAL_TIMEOUT_S
    transport: httpx.BaseTransport | None = None
    mock_handler: Callable[[str, str], str] | None = None
    mock_served_model: str | None = None
    trace: Any | None = None
    max_tokens: int = DEFAULT_MAX_TOKENS
    extra_body: dict[str, Any] | None = None
    last_routing: dict[str, str] = field(default_factory=dict)
    deep_model: str | None = None
    deep_steps: tuple[str, ...] | None = None
    deep_extra_body: dict[str, Any] | None = None
    nemotron_policy: str | None = None
    nvidia_api_key: str | None = None
    nvidia_base_url: str | None = None
    nvidia_model: str | None = None
    nvidia_transport: httpx.BaseTransport | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        # Environment overrides apply only to values the caller left at the default, so a
        # caller (or a test) that passes an explicit timeout keeps it.
        attempt = _env_positive("PHOENIX_LLM_ATTEMPT_TIMEOUT_S")
        if attempt is not None:
            if self.wall_clock_timeout == DEFAULT_ATTEMPT_TIMEOUT_S:
                self.wall_clock_timeout = attempt
            if self.timeout == DEFAULT_READ_TIMEOUT_S:
                self.timeout = attempt
        total = _env_positive("PHOENIX_LLM_TOTAL_TIMEOUT_S")
        if total is not None and self.total_timeout == DEFAULT_TOTAL_TIMEOUT_S:
            self.total_timeout = total
        tokens = _env_positive("PHOENIX_LLM_MAX_TOKENS")
        if tokens is not None and self.max_tokens == DEFAULT_MAX_TOKENS:
            self.max_tokens = int(tokens)
        if self.extra_body is None:
            self.extra_body = extra_body_from_env()
        else:
            self.extra_body = {key: item for key, item in self.extra_body.items() if key not in _PROTECTED_BODY_KEYS}
        if self.deep_model is None:
            self.deep_model = _env_text("PHOENIX_DEEP_MODEL", "MODEL_NAME_DEEP") or None
        if self.deep_steps is None:
            raw_steps = _env_text("PHOENIX_DEEP_STEPS")
            self.deep_steps = tuple(part.strip() for part in raw_steps.split(",") if part.strip()) if raw_steps else DEFAULT_DEEP_STEPS
        if self.deep_extra_body is None:
            self.deep_extra_body = extra_body_from_env("PHOENIX_LLM_DEEP_EXTRA_BODY")
        else:
            self.deep_extra_body = {key: item for key, item in self.deep_extra_body.items() if key not in _PROTECTED_BODY_KEYS}
        if self.nemotron_policy is None:
            self.nemotron_policy = nemotron_policy_from_env()
        elif self.nemotron_policy not in NEMOTRON_POLICIES:
            raise ValueError(f"nemotron_policy must be one of {NEMOTRON_POLICIES}")
        if self.nemotron_policy == "fallback-nvidia":
            if self.nvidia_api_key is None:
                self.nvidia_api_key = _env_text("NVIDIA_API_KEY") or None
                if self.nvidia_api_key is None:
                    try:
                        from config.settings import Settings

                        self.nvidia_api_key = Settings().nvidia_api_key
                    except Exception:  # noqa: BLE001 — a malformed .env leaves the key unset
                        self.nvidia_api_key = None
            self.nvidia_base_url = (self.nvidia_base_url or _env_text("NVIDIA_BASE_URL") or DEFAULT_NVIDIA_BASE_URL).rstrip("/")
            self.nvidia_model = self.nvidia_model or _env_text("NVIDIA_MODEL") or None
        self.rejected_serves: int = 0
        self.direct_calls: int = 0
        self.last_reasoning: str = ""
        if self.token is None:
            self.token = os.environ.get("METERLESS_RUNTIME_TOKEN") or ""
        if self.cache_dir is None and os.environ.get("PHOENIX_LLM_CACHE_DIR"):
            self.cache_dir = Path(os.environ["PHOENIX_LLM_CACHE_DIR"])
        if self.cache_dir is None:
            self.cache_dir = ROOT / ".cache" / "relevance"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.served_models: set[str] = set()
        self.model_calls: dict[str, int] = {}
        self.repo_mismatches: set[str] = set()
        self.mismatch_count: int = 0
        self.last_cache_hit: bool = False
        self.last_cache_file: Path | None = None
        self._call_noted: bool = False

    def _call_label(self, served: str) -> str:
        return served if served else "(missing)"

    def route(self, step: str) -> tuple[str, dict[str, Any], str]:
        """(model, extra body, tier) for one step. tier is "deep" or "fast"."""
        if self.deep_model and (step or "") in (self.deep_steps or ()):
            return self.deep_model, dict(self.deep_extra_body or {}), "deep"
        return self.model, dict(self.extra_body or {}), "fast"

    def _observe(
        self,
        requested: str,
        served: str,
        repo_id: str,
        step: str,
        status: str = "ok",
        routing: dict[str, str] | None = None,
        *,
        provider: str = "nightshift",
        tier: str = "",
        reasoning: str = "",
        seconds: float | None = None,
        cache_hit: bool = False,
    ) -> None:
        """Count one model reply and, when a trace is attached, write one LLM_CALL line.

        served is stored as the provider returned it. A missing model stays empty and is
        counted under "(missing)". It is not replaced with the requested id. A reply that the
        Nemotron policy discarded (status "rejected") is traced but not counted as used.
        """

        self._call_noted = True
        if status == "rejected":
            self.rejected_serves += 1
        else:
            label = self._call_label(served)
            self.served_models.add(label)
            self.model_calls[label] = self.model_calls.get(label, 0) + 1
            if model_mismatch(served):
                self.mismatch_count += 1
                if repo_id:
                    self.repo_mismatches.add(repo_id)
        if self.trace is None:
            return
        summary: dict[str, Any] = {"requested_model": requested, "served_model": served}
        if provider != "nightshift":
            summary["provider"] = provider
        if tier:
            summary["tier"] = tier
        if routing:
            summary["routing"] = routing
        if seconds is not None:
            summary["elapsed_s"] = round(float(seconds), 2)
        if cache_hit:
            summary["cache_hit"] = True
        if reasoning:
            summary["reasoning"] = reasoning
        self.trace.event(
            agent_name="orchestrator",
            action="LLM_CALL",
            input_summary={"repo_id": repo_id, "step": step or ""},
            output_summary=summary,
            llm={"requested_model": requested, "served_model": served},
            status=status,
            summary_limit=4000 if reasoning else 500,
        )

    def _log_served(self, repo_id: str, step: str, served: str, elapsed: float, cache_hit: bool) -> None:
        shown = served if served else "(missing)"
        label = "cache_hit " if cache_hit else ""
        sys.stderr.write(f"[{repo_id or 'unknown'}] step={step or 'unknown'} {label}served={shown} {elapsed:.2f}s\n")
        sys.stderr.flush()

    def discard_last(self) -> None:
        """Delete the cache entry of the last call, so an unusable answer is never replayed."""
        f = self.last_cache_file
        self.last_cache_file = None
        if f is not None:
            try:
                f.unlink()
            except OSError:
                pass

    def complete_json(
        self,
        prompt: str,
        system_prompt: str = "",
        repo_id: str = "",
        step: str = "",
        attempts: int = 3,
        validate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> tuple[dict[str, Any] | None, str, str, str]:
        """Call the model until it returns a usable JSON object.

        An unusable answer (invalid JSON, or failing `validate`) is removed from the cache and
        the call is repeated with a short corrective note, so the cache key differs and a bad
        answer can never be replayed. Returns (obj_or_None, text, requested_model, served_model).
        Transport errors propagate to the caller.
        """
        last = ("", self.model, "")
        for n in range(max(1, attempts)):
            p = prompt if n == 0 else (
                prompt + f"\n\n(Retry {n}: your previous reply could not be used. Reply with ONLY one strict "
                "JSON object that follows the schema exactly: no prose, no code fences.)"
            )
            text, req_m, srv_m = self.complete(p, system_prompt, repo_id=repo_id, step=step)
            obj = extract_json_object(text)
            if obj is not None and (validate is None or validate(obj)):
                return obj, text, req_m, srv_m
            self.discard_last()
            last = (text, req_m, srv_m)
        return None, last[0], last[1], last[2]

    def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        repo_id: str = "",
        step: str = "",
        call_budget_s: float | None = None,
    ) -> tuple[str, str, str]:
        """Return (response_text, requested_model, served_model)."""

        self._call_noted = False
        try:
            return self._complete(
                prompt, system_prompt, repo_id=repo_id, step=step, call_budget_s=call_budget_s,
            )
        except RelevanceLLMError:
            if not self._call_noted:
                # The join already gave up. Record the requested id and an empty serve.
                # Do not copy a model id from a response that arrived after the cap.
                self._observe(self.route(step)[0], "", repo_id, step, status="error")
            raise

    def _complete(
        self,
        prompt: str,
        system_prompt: str = "",
        repo_id: str = "",
        step: str = "",
        call_budget_s: float | None = None,
    ) -> tuple[str, str, str]:
        call_start = time.monotonic()
        model, body_extra, tier = self.route(step)
        tier_label = tier if self.deep_model else ""
        total_cap = self.total_timeout if call_budget_s is None else min(self.total_timeout, max(0.0, call_budget_s))
        prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()
        input_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        cache_key = hashlib.sha256(f"{model}\n---\n{prompt_hash}\n---\n{input_hash}".encode("utf-8")).hexdigest()
        cache_file = self.cache_dir / f"{cache_key}.json"
        self.last_reasoning = ""

        if cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                raw_served = cached.get("served_model")
                served = raw_served.strip() if isinstance(raw_served, str) else ""
                if served.startswith("local-runtime/"):
                    self._observe(model, served, repo_id, step, status="error")
                    raise RelevanceLLMError(
                        f"Nightshift served local model {served}; refusing to score with a small local model"
                    )
                if self.nemotron_policy != "label" and model_mismatch(served):
                    # Written under the label policy. Under require / fallback-nvidia a
                    # non-Nemotron answer is not reused; it is asked again below.
                    raise ValueError("cached non-Nemotron answer under a Nemotron-only policy")
                req_raw = cached.get("requested_model")
                req_m = req_raw.strip() if isinstance(req_raw, str) and req_raw.strip() else model
                cached_routing = cached.get("routing") if isinstance(cached.get("routing"), dict) else {}
                self.last_routing = dict(cached_routing)
                self.last_reasoning = str(cached.get("reasoning") or "")
                self._observe(
                    req_m, served, repo_id, step, routing=cached_routing or None,
                    provider=str(cached.get("provider") or "nightshift"), tier=tier_label,
                    reasoning=self.last_reasoning if tier == "deep" else "", cache_hit=True,
                )
                self.last_cache_hit = True
                self.last_cache_file = cache_file
                self._log_served(repo_id, step, served, time.monotonic() - call_start, cache_hit=True)
                return cached.get("response_text", ""), req_m, served
            except RelevanceLLMError:
                raise
            except Exception:
                pass

        def _store(text: str, served: str, routing: dict[str, str], provider: str, reasoning: str) -> tuple[str, str, str]:
            record = {
                "cache_key": cache_key,
                "model": model,
                "prompt_sha256": prompt_hash,
                "input_sha256": input_hash,
                "requested_model": model,
                "served_model": served,
                "provider": provider,
                "routing": routing,
                "reasoning": reasoning,
                "response_text": text,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            cache_file.write_text(json.dumps(record, indent=2), encoding="utf-8")
            self.last_reasoning = reasoning
            self._observe(
                model, served, repo_id, step, routing=routing or None, provider=provider,
                tier=tier_label, reasoning=reasoning if tier == "deep" else "",
                seconds=time.monotonic() - call_start,
            )
            self.last_cache_hit = False
            self.last_cache_file = cache_file
            self._log_served(repo_id, step, served, time.monotonic() - call_start, cache_hit=False)
            return text, model, served

        if self.mock_handler is not None:
            # A mock that does not name a served model is declaring that it served the request.
            # An explicit empty string is a missing model field and stays empty.
            served = model if self.mock_served_model is None else self.mock_served_model
            if served.startswith("local-runtime/"):
                self._observe(model, served, repo_id, step, status="error")
                raise RelevanceLLMError(
                    f"Nightshift served local model {served}; refusing to score with a small local model"
                )
            resp_text = self.mock_handler(prompt, system_prompt)
            return _store(resp_text, served, {}, "nightshift", "")

        if not self.token or self.token == "rt-local":
            raise RelevanceLLMError("METERLESS_RUNTIME_TOKEN is missing or invalid.")

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "meterless-app": "phoenixrepo",
            "meterless-scope": "user",
        }
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "stream": False,
            "max_tokens": int(self.max_tokens),
        }
        payload.update(body_extra)

        if isinstance(self.timeout, (int, float)):
            configured_read = float(self.timeout)
            client_timeout = httpx.Timeout(connect=10.0, read=configured_read, write=30.0, pool=10.0)
        elif isinstance(self.timeout, httpx.Timeout):
            configured_read = float(self.timeout.read or DEFAULT_READ_TIMEOUT_S)
            client_timeout = self.timeout
        else:
            configured_read = DEFAULT_READ_TIMEOUT_S
            client_timeout = httpx.Timeout(connect=10.0, read=configured_read, write=30.0, pool=10.0)

        def _do_post(timeout: httpx.Timeout = client_timeout) -> httpx.Response:
            with httpx.Client(timeout=timeout, transport=self.transport) as client:
                return client.post(url, headers=headers, json=payload)

        def _direct(reason: str) -> tuple[str, str, str] | None:
            """Same Nemotron model at the NVIDIA API, only under fallback-nvidia. None = not used or failed."""
            if self.nemotron_policy != "fallback-nvidia" or not self.nvidia_api_key:
                return None
            remaining = total_cap - (time.monotonic() - call_start)
            if remaining <= 1.0:
                return None
            direct_model = self.nvidia_model or model
            body = {**payload, "model": direct_model}
            limit = max(1.0, min(self.wall_clock_timeout, remaining))
            try:
                with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=limit, write=30.0, pool=10.0), transport=self.nvidia_transport) as client:
                    resp = client.post(
                        f"{self.nvidia_base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.nvidia_api_key}", "Content-Type": "application/json"},
                        json=body,
                    )
            except httpx.HTTPError as exc:
                self._observe(direct_model, "", repo_id, step, status="error", provider="nvidia-direct", tier=tier_label)
                sys.stderr.write(f"[{repo_id or 'unknown'}] step={step} nvidia-direct failed after {reason}: {type(exc).__name__}\n")
                return None
            if resp.status_code != 200:
                self._observe(direct_model, "", repo_id, step, status="error", provider="nvidia-direct", tier=tier_label)
                sys.stderr.write(f"[{repo_id or 'unknown'}] step={step} nvidia-direct HTTP {resp.status_code} after {reason}\n")
                return None
            try:
                data = resp.json()
            except ValueError:
                return None
            raw = data.get("model") if isinstance(data, dict) else None
            served = raw.strip() if isinstance(raw, str) else ""
            if model_mismatch(served):
                self._observe(direct_model, served, repo_id, step, status="rejected", provider="nvidia-direct", tier=tier_label)
                return None
            msg, text = _message_text(data)
            self.direct_calls += 1
            return _store(text, served, {"fallback_reason": reason[:120]}, "nvidia-direct", reasoning_excerpt(msg, text))

        last_exc: Exception | None = None
        for delay in RETRY_DELAYS:
            now = time.monotonic()
            if now - call_start + delay > total_cap:
                raise last_exc if isinstance(last_exc, RelevanceLLMError) and "not Nemotron" in str(last_exc) else RelevanceLLMError(
                    f"Nightshift call exceeded total timeout limit of {total_cap}s across retries."
                )
            if delay > 0:
                time.sleep(delay)

            # Discovery calls keep the 120s wall. An alternatives call passes call_budget_s,
            # and a healthy Nightshift judgement can take several minutes, so that attempt
            # may use the remaining budget up to total_timeout. A short budget stays short.
            elapsed = time.monotonic() - call_start
            remaining_cap = total_cap - elapsed
            if call_budget_s is None:
                attempt_cap = self.wall_clock_timeout
            else:
                attempt_cap = max(self.wall_clock_timeout, total_cap)
            time_left = max(0.01, min(attempt_cap, remaining_cap))
            attempt_timeout = client_timeout
            if call_budget_s is not None and time_left > configured_read:
                attempt_timeout = httpx.Timeout(connect=10.0, read=time_left, write=30.0, pool=10.0)

            # The worker only fills its own box. Cache, requested_model, and served_model are
            # written on this thread after a successful join, so an abandoned HTTP 502 or a late
            # 200 cannot overwrite provenance for the call that the fallback cap already gave up on.
            box: dict[str, Any] = {}

            def run(box: dict[str, Any] = box, timeout: httpx.Timeout = attempt_timeout) -> None:
                try:
                    box["resp"] = _do_post(timeout)
                except Exception as exc:
                    box["error"] = exc

            worker = threading.Thread(target=run, name="nightshift-relevance", daemon=True)
            worker.start()
            worker.join(time_left)
            if worker.is_alive():
                rescued = _direct("nightshift timeout")
                if rescued is not None:
                    return rescued
                raise RelevanceLLMError(
                    f"Nightshift request timed out (wall-clock cap of {time_left:.2f}s exceeded)."
                )
            if "error" in box:
                exc = box["error"]
                if isinstance(exc, httpx.HTTPError):
                    last_exc = RelevanceLLMError(f"Nightshift request failed: {exc}")
                    rescued = _direct("nightshift transport error")
                    if rescued is not None:
                        return rescued
                    continue
                if isinstance(exc, RelevanceLLMError):
                    raise
                last_exc = RelevanceLLMError(str(exc))
                continue
            resp = box.get("resp")
            if resp is None:
                last_exc = RelevanceLLMError("Nightshift request returned no response.")
                continue

            if resp.status_code == 200:
                data = resp.json()
                raw_served = data.get("model") if isinstance(data, dict) else None
                served = raw_served.strip() if isinstance(raw_served, str) else ""
                if served.startswith("local-runtime/"):
                    self._observe(model, served, repo_id, step, status="error")
                    raise RelevanceLLMError(
                        f"Nightshift served local model {served}; refusing to score with a small local model"
                    )
                routing = routing_meta(data, getattr(resp, "headers", None))
                self.last_routing = routing
                msg, text = _message_text(data)
                if self.nemotron_policy != "label" and model_mismatch(served):
                    # The answer exists but was not written by Nemotron: it is traced and discarded.
                    self._observe(
                        model, served, repo_id, step, status="rejected", routing=routing or None,
                        tier=tier_label, seconds=time.monotonic() - call_start,
                    )
                    rescued = _direct(f"nightshift served {served or '(missing)'}")
                    if rescued is not None:
                        return rescued
                    last_exc = RelevanceLLMError(
                        f"Nightshift served {served or '(missing)'}, not Nemotron; PHOENIX_NEMOTRON_POLICY={self.nemotron_policy} discarded it"
                    )
                    continue
                return _store(text, served, routing, "nightshift", reasoning_excerpt(msg, text))

            if resp.status_code in (401, 403):
                raise RelevanceLLMError(f"Nightshift returned HTTP {resp.status_code}: Unauthorized")
            last_exc = RelevanceLLMError(f"Nightshift returned HTTP {resp.status_code}")
            if resp.status_code >= 500:
                rescued = _direct(f"nightshift HTTP {resp.status_code}")
                if rescued is not None:
                    return rescued

        raise last_exc or RelevanceLLMError("Nightshift request failed after retries.")

def is_valid_demand_signal_url(url: str, candidate_repo_id: str) -> tuple[bool, str]:
    """Validate signal URL: must be issue/discussion/forum, not PR, code file, repo root or candidate repo."""
    clean = str(url or "").strip()
    if not clean:
        return False, "Signal URL is empty"
    low = clean.lower()

    # Reject candidate's own repository
    cand_low = candidate_repo_id.lower().strip()
    if cand_low and cand_low in low:
        return False, f"Signal URL is from candidate repo {candidate_repo_id}"

    # Reject PRs
    if "/pull/" in low or "/pulls/" in low:
        return False, "Signal URL is a pull request"

    # Reject code files and trees
    if "/blob/" in low or "/tree/" in low:
        return False, "Signal URL is a code file or directory"

    # For GitHub URLs: must be issue or discussion
    if "github.com" in low or "api.github.com" in low:
        if not any(k in low for k in ("/issues", "/discussions", "is:issue")):
            return False, "Signal URL is a repository page, not an issue or discussion"

    return True, ""


def extract_capabilities(
    candidate: dict[str, Any],
    client: RelevanceClient | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Extract up to 5 user-facing capabilities with exact README quotes.

    Code rejects:
    - Any quote not an exact substring of stored README.
    - Any capability made only of repo-name/owner tokens or generic words.
    """
    repo_id = str(candidate.get("repo_id") or "").strip()
    readme = str(candidate.get("readme_excerpt") or "").strip()
    if not readme:
        # Fallback to description + problem excerpt if readme excerpt is empty
        readme = str(candidate.get("description") or candidate.get("problem_excerpt") or "").strip()
    if not readme:
        return [], {"requested_model": "", "served_model": "", "fallback": False}

    if client is None:
        client = RelevanceClient()

    prompt_record = load_prompt("relevance_capabilities")
    user_prompt = (
        f"Candidate Repository: {repo_id}\n"
        f"Stored README Excerpt:\n```\n{readme}\n```\n\n"
        "Extract up to 5 user-facing capabilities as strict JSON."
    )

    try:
        text, req_m, srv_m = client.complete(user_prompt, prompt_record.text, repo_id=repo_id, step="capabilities")
    except Exception as exc:
        if isinstance(exc, RelevanceLLMError) and "refusing to score with a small local model" in str(exc):
            raise
        return [], {
            "requested_model": getattr(client, "model", DEFAULT_NIGHTSHIFT_MODEL),
            "served_model": "",
            "fallback": True,
            "error": str(exc),
        }

    obj = extract_json_object(text)
    if obj is None or not isinstance(obj.get("capabilities"), list):
        # Retry once for strict JSON
        try:
            retry_prompt = user_prompt + "\n\nError: Previous output was not valid JSON. Output strictly JSON matching the schema."
            text, req_m, srv_m = client.complete(retry_prompt, prompt_record.text, repo_id=repo_id, step="capabilities")
            obj = extract_json_object(text)
        except Exception as exc:
            if isinstance(exc, RelevanceLLMError) and "refusing to score with a small local model" in str(exc):
                raise

    if obj is None or not isinstance(obj.get("capabilities"), list):
        return [], {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": True,
            "error": "LLM output invalid JSON",
        }

    # Extract tokens from repo name and owner
    repo_tokens: set[str] = set()
    for segment in repo_id.lower().replace("-", " ").replace("_", " ").split():
        repo_tokens.update(re.findall(r"[a-z0-9]+", segment))
    for t in list(repo_tokens):
        repo_tokens.update(re.findall(r"[a-z]+|[0-9]+", t))

    verified: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for item in obj.get("capabilities", []):
        if len(verified) >= 5:
            break
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        quote = str(item.get("quote") or "").strip()
        if not name or not quote:
            continue
        if name.lower() in seen_names:
            continue

        # 1. Quote must be exact substring of stored text
        if quote not in readme:
            continue

        # 2. Reject if made only of repo-name/owner tokens or generic words
        words = set(re.findall(r"[a-z0-9]+", name.lower()))
        meaningful = words - STOPWORDS
        if not meaningful:
            continue
        if meaningful.issubset(repo_tokens | GENERIC_WORDS):
            continue

        seen_names.add(name.lower())
        verified.append({"name": name, "quote": quote})

    return verified, {"requested_model": req_m, "served_model": srv_m, "fallback": False}


def judge_demand(
    candidate_capabilities: list[dict[str, str]],
    signal: dict[str, Any],
    candidate_repo_id: str,
    client: RelevanceClient | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Judge if an external user signal represents demand for any candidate capability.

    Code accepts only if quote is an exact substring of signal text,
    capability is in candidate_capabilities, and URL is valid non-candidate issue/discussion.
    """
    url = str(signal.get("url") or "").strip()
    valid, reason = is_valid_demand_signal_url(url, candidate_repo_id)
    if not valid:
        return None, {"error": reason, "fallback": False}

    if not candidate_capabilities:
        return None, {"error": "No verified candidate capabilities to match", "fallback": False}

    signal_text = str(signal.get("text") or signal.get("excerpt") or "").strip()
    if not signal_text:
        return None, {"error": "Signal text is empty", "fallback": False}

    if client is None:
        client = RelevanceClient()

    prompt_record = load_prompt("relevance_demand")
    caps_str = "\n".join(f"- {c['name']} (quote: \"{c['quote']}\")" for c in candidate_capabilities)
    user_prompt = (
        f"Candidate Capabilities:\n{caps_str}\n\n"
        f"Signal URL: {url}\n"
        f"Signal Text:\n```\n{signal_text}\n```\n\n"
        "Judge if this signal represents demand for one of the candidate capabilities. Strict JSON output."
    )

    try:
        text, req_m, srv_m = client.complete(
            user_prompt, prompt_record.text, repo_id=candidate_repo_id, step="demand"
        )
    except Exception as exc:
        if isinstance(exc, RelevanceLLMError) and "refusing to score with a small local model" in str(exc):
            raise
        return None, {
            "requested_model": getattr(client, "model", DEFAULT_NIGHTSHIFT_MODEL),
            "served_model": "",
            "fallback": True,
            "error": str(exc),
        }

    obj = extract_json_object(text)
    if obj is None:
        return None, {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": True,
            "error": "LLM output invalid JSON",
        }

    relevant = bool(obj.get("relevant"))
    if not relevant:
        return None, {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": False,
            "reason": "Judged not relevant",
        }

    quote = str(obj.get("quote") or "").strip()
    if not quote or quote not in signal_text:
        return None, {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": False,
            "reason": "Quote not an exact substring of signal text",
        }

    cap_name = str(obj.get("capability") or "").strip().lower()
    matched_cap = None
    for c in candidate_capabilities:
        c_low = c["name"].lower()
        if c_low == cap_name or c_low in cap_name or cap_name in c_low:
            matched_cap = c
            break

    if not matched_cap:
        return None, {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": False,
            "reason": f"Capability '{obj.get('capability')}' not in candidate capability list",
        }

    result = {
        "relevant": True,
        "capability": matched_cap["name"],
        "quote": quote,
        "why": str(obj.get("why") or ""),
        "url": url,
        "signal_id": str(signal.get("signal_id") or ""),
    }
    return result, {"requested_model": req_m, "served_model": srv_m, "fallback": False}


def judge_alternative_coverage(
    capability: dict[str, str],
    alternative: dict[str, Any],
    client: RelevanceClient | None = None,
    repo_id: str = "",
) -> tuple[bool, str, dict[str, Any]]:
    """Judge if a maintained free alternative covers a candidate capability.

    Code verifies the quote is an exact substring of alternative's text.
    """
    alt_text = str(alternative.get("excerpt") or alternative.get("text") or "").strip()
    if not alt_text:
        return False, "", {"error": "Alternative text is empty", "fallback": False}

    if client is None:
        client = RelevanceClient()

    prompt_record = load_prompt("relevance_coverage")
    user_prompt = (
        f"Candidate Capability: {capability['name']}\n"
        f"Candidate Quote: \"{capability['quote']}\"\n\n"
        f"Alternative ({alternative.get('alternative_id', '')}) Text:\n```\n{alt_text}\n```\n\n"
        "Judge if the alternative covers this capability. Strict JSON output."
    )

    try:
        text, req_m, srv_m = client.complete(
            user_prompt, prompt_record.text, repo_id=repo_id, step="coverage"
        )
    except Exception as exc:
        if isinstance(exc, RelevanceLLMError) and "refusing to score with a small local model" in str(exc):
            raise
        return False, "", {
            "requested_model": getattr(client, "model", DEFAULT_NIGHTSHIFT_MODEL),
            "served_model": "",
            "fallback": True,
            "error": str(exc),
        }

    obj = extract_json_object(text)
    if obj is None:
        return False, "", {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": True,
            "error": "LLM output invalid JSON",
        }

    covered = bool(obj.get("covered"))
    if not covered:
        return False, "", {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": False,
            "why": str(obj.get("why") or "Not covered"),
        }

    quote = str(obj.get("quote") or "").strip()
    if not quote or quote not in alt_text:
        return False, "", {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": False,
            "reason": "Covered quote not found in alternative text",
        }

    return True, quote, {
        "requested_model": req_m,
        "served_model": srv_m,
        "fallback": False,
        "why": str(obj.get("why") or ""),
    }


def is_client_configured(client: RelevanceClient | None) -> bool:
    """Check if a RelevanceClient is configured and able to make LLM calls."""
    if client is None:
        token = os.environ.get("METERLESS_RUNTIME_TOKEN")
        return bool(token and token != "rt-local")
    if client.mock_handler is not None:
        return True
    return bool(client.token and client.token != "rt-local")


def normalize_whitespace(text: str) -> str:
    """Normalize repeated whitespace and line breaks to single spaces."""
    return " ".join(str(text or "").split())


_MARKUP_TRANS = str.maketrans({
    "*": " ", "_": " ", "`": " ", "#": " ", ">": " ",
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-",
})


def _normalize_for_quote(text: str) -> str:
    """Strip Markdown emphasis/heading marks and typographic quotes, then collapse whitespace.

    Deterministic: the words and their order must still match exactly. This only stops
    "**Rammingen** is ..." in a README from failing against the quote "Rammingen is ...".
    """
    return normalize_whitespace(str(text or "").translate(_MARKUP_TRANS)).casefold()


def quote_in_text(quote: str, text: str) -> bool:
    """Deterministic quote verification: case-insensitive substring check after normalising
    whitespace, Markdown emphasis marks and typographic quotes."""
    if not quote or not text:
        return False
    if normalize_whitespace(quote).casefold() in normalize_whitespace(text).casefold():
        return True
    nq = _normalize_for_quote(quote)
    return bool(nq) and nq in _normalize_for_quote(text)


def build_search_queries(candidate: dict[str, Any], problem_statement: dict[str, Any]) -> list[str]:
    """Build 4-8 problem-oriented search queries describing the job, not repo words.

    Must include at least:
    - <platform> cli
    - <platform> <job> tool (e.g. 'youtrack export markdown')
    - <platform> api client
    - official-feature query (<platform> export <format> / <platform> built-in <job>)
    """
    known_platforms = [
        "youtrack", "jira", "github", "gitlab", "notion", "confluence", "slack",
        "discord", "obsidian", "trello", "linear", "clickup", "airtable", "salesforce",
        "zendesk", "shopify", "stripe", "docker", "kubernetes", "aws", "gcp",
        "azure", "postgres", "sqlite", "mysql", "redis", "mongodb", "elastic",
        "grafana", "prometheus", "sentry", "bitbucket", "jenkins", "circleci",
        "ansible", "terraform", "vault", "kafka", "rabbitmq",
    ]
    known_formats = ["markdown", "json", "csv", "pdf", "html", "yaml", "xml", "text", "excel"]
    known_jobs = ["export", "sync", "import", "backup", "edit", "fetch", "generate", "publish", "migrate", "archive"]

    category = str(problem_statement.get("category") or "").strip().lower()
    desc = str(candidate.get("description") or "").lower()
    readme = str(candidate.get("readme_excerpt") or "").lower()
    prob = str(problem_statement.get("problem") or "").lower()
    user = str(problem_statement.get("target_user") or "").lower()
    cand_id = str(candidate.get("repo_id") or "").lower()
    cand_text = f"{cand_id} {desc} {readme} {prob} {user} {category}"

    # 1. Identify platform
    platform = ""
    for p in known_platforms:
        if re.search(r"\b" + re.escape(p) + r"\b", cand_text):
            platform = p
            break
    if not platform:
        m = re.search(r"(?:using|for|with|in)\s+([a-zA-Z0-9_-]+)", f"{user} {prob}", re.I)
        if m and m.group(1).lower() not in {"the", "a", "an", "all", "our", "their", "your", "my", "this", "that"}:
            platform = m.group(1).lower()
    if not platform and candidate.get("core_terms"):
        terms = [t.strip().lower() for t in str(candidate.get("core_terms")).split(",") if t.strip()]
        generic = {"tool", "cli", "app", "sync", "export", "generator", "manager", "service", "system", "client", "library", "markdown", "python", "javascript", "workflow", "workflows"}
        for t in terms:
            if t not in generic and len(t) > 2:
                platform = t
                break
    if not platform:
        platform = category.split("-")[0].lower() if category else "developer"

    # 2. Identify format
    format_str = ""
    for f in known_formats:
        if re.search(r"\b" + re.escape(f) + r"\b", cand_text) or (f == "markdown" and re.search(r"\bmd\b", cand_text)):
            format_str = f
            break
    if not format_str:
        format_str = "markdown" if "markdown" in cand_text else "json"

    # 3. Identify job
    job_word = ""
    for j in known_jobs:
        if re.search(r"\b" + re.escape(j) + r"\b", cand_text):
            job_word = j
            break
    if not job_word:
        job_word = "export" if "export" in cand_text else "sync"

    # Required patterns
    pattern_queries: list[str] = [
        f"{platform} cli",
        f"{platform} {job_word} {format_str}" if format_str in cand_text else f"{platform} {job_word} tool",
        f"{platform} api client",
        f"{platform} export {format_str}",
        f"{platform} built-in {job_word}",
    ]

    # Raw search queries from LLM
    raw_queries = problem_statement.get("search_queries") or []
    llm_queries = [str(q).strip() for q in raw_queries if str(q).strip()]

    # Self-hosted / open-source queries
    extra_queries: list[str] = []
    is_self_hosted = any(k in cand_text for k in ("self-hosted", "self hosted", "selfhosted"))
    if is_self_hosted and category:
        extra_queries.append(f"self-hosted {category}")
        extra_queries.append(f"open source {category}")

    stack_components = {
        "postgres", "sqlite", "mysql", "redis", "mongodb", "elastic", "docker", "kubernetes",
        "aws", "gcp", "azure", "kafka", "rabbitmq", "developer",
    }
    identity_text = f"{cand_id} {desc}"
    if platform in stack_components or not re.search(r"\b" + re.escape(platform) + r"\b", identity_text):
        # A database/infra word is how the repo is built, not the job it does; and a platform
        # only mentioned somewhere in the README (e.g. "github") is not what the tool is for.
        pattern_queries = []
    # Interleave so both the user's job (LLM) and platform patterns get searched early.
    interleaved: list[str] = []
    for i in range(max(len(llm_queries), len(pattern_queries))):
        if i < len(llm_queries):
            interleaved.append(llm_queries[i])
        if i < len(pattern_queries):
            interleaved.append(pattern_queries[i])
    all_queries = interleaved + extra_queries

    # Deduplicate preserving order
    seen = set()
    dedup_queries: list[str] = []
    for q in all_queries:
        qk = q.lower()
        if qk not in seen:
            seen.add(qk)
            dedup_queries.append(q)

    # Product-discovery queries: the way a person looks for an existing solution ("best X tools",
    # "X alternatives"). They surface mainstream products and comparison pages that keyword
    # queries about the job miss. Placed early so the 8-query cap never drops them.
    if llm_queries:
        product_queries = []
        for raw_job in llm_queries[:2]:
            job = re.sub(r"\s+", " ", raw_job).strip()
            job = re.sub(r"\s+(tools?|software|apps?)$", "", job, flags=re.I)
            if job:
                product_queries.append(f"best {job} tools")
        for pq in reversed(product_queries):
            if pq.lower() not in seen:
                seen.add(pq.lower())
                dedup_queries.insert(min(2, len(dedup_queries)), pq)

    # Ensure 4 to 8 queries
    if len(dedup_queries) < 4:
        if platform in stack_components:
            cat_words = category.replace("-", " ").strip()
            fallbacks = [f for f in [f"{cat_words} open source" if cat_words else "", f"{llm_queries[0]} alternative" if llm_queries else "", f"{cat_words} saas" if cat_words else ""] if f]
        else:
            fallbacks = [f"{platform} tool", f"{platform} open source", f"{category} tool"]
        for fallback in fallbacks:
            if fallback.lower() not in seen:
                seen.add(fallback.lower())
                dedup_queries.append(fallback)
            if len(dedup_queries) >= 4:
                break

    return dedup_queries[:8]


def broaden_queries(queries: list[str], category: str = "", candidate_repo_id: str = "") -> list[str]:
    """Generate broader search queries by dropping the most specific term and adding category."""
    broader: list[str] = []
    cand_tokens = {t.lower() for t in re.findall(r"\w+", candidate_repo_id)}
    cat_clean = category.strip().lower()

    for q in queries:
        words = q.split()
        if len(words) <= 1:
            if cat_clean and cat_clean not in q.lower():
                broader.append(f"{q} {cat_clean}".strip())
            continue

        # Drop the most specific term: first check if any word matches candidate tokens
        dropped = False
        remaining = []
        for w in words:
            if not dropped and w.lower() in cand_tokens:
                dropped = True
                continue
            remaining.append(w)

        # If no candidate token was in the query, drop the longest word (most specific)
        if not dropped and remaining:
            longest = max(remaining, key=len)
            remaining.remove(longest)

        broader_q = " ".join(remaining).strip()
        if broader_q:
            broader.append(broader_q)
            if cat_clean and cat_clean not in broader_q.lower():
                broader.append(f"{broader_q} {cat_clean}".strip())

    if cat_clean and not any(cat_clean == b.lower() for b in broader):
        broader.append(cat_clean)

    # Deduplicate while preserving order and excluding original queries
    orig_set = {q.lower() for q in queries}
    seen = set()
    dedup_broader: list[str] = []
    for b in broader:
        bk = b.lower()
        if bk not in seen and bk not in orig_set:
            seen.add(bk)
            dedup_broader.append(b)
    return dedup_broader


class DiscoveryDecision(tuple):
    """`(verdict, reason)` plus the alternatives that decided an ALREADY_EXISTS or NEEDS_REVIEW.

    Callers unpack two strings. `evidence` is the list a report shows: alternative name,
    overlap, the quote that was checked, and the reason.
    """

    def __new__(
        cls,
        verdict: str,
        reason: str,
        evidence: list[dict[str, str]] | None = None,
        must_beat: list[dict[str, str]] | None = None,
    ):
        self = super().__new__(cls, (verdict, reason))
        self.evidence = list(evidence or [])
        self.must_beat = list(must_beat or [])
        return self


def _evidence_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        quotes = [str(q).strip() for q in (r.get("quotes") or []) if str(q).strip()]
        if not quotes and str(r.get("quote") or "").strip():
            quotes = [str(r.get("quote")).strip()]
        name = str(r.get("alternative_id") or r.get("alternative") or r.get("url") or "").strip()
        overlap = str(r.get("overlap") or "").strip()
        why = str(r.get("reason") or "").strip()
        quote = quotes[0] if quotes else ""
        if not name and not quote:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append({"alternative": name, "overlap": overlap, "quote": quote, "reason": why})
    return out


def judge_discovery(
    candidate: dict[str, Any],
    prior_rows: list[dict[str, Any]],
    demand_rows: list[dict[str, Any]],
    client: RelevanceClient | None = None,
    problem_alternatives: list[dict[str, Any]] | None = None,
    source_status: dict[str, str] | None = None,
    source_counts: dict[str, int] | None = None,
    insufficient_coverage: bool = False,
) -> tuple[str, str]:
    """Judge whether candidate is CANDIDATE, ALREADY_EXISTS, or NO_GO using LLM with deterministic quote verification.

    A verified full overlap refuses the repository only when the alternative also passes the
    maturity rule (maintained, mature or official, matching access). Stars are used for that
    rule, not as demand. An alternative that fails it is recorded on ``must_beat`` and does
    not refuse the repository.
    Verifies alternative_repo against the union of prior_rows and problem_alternatives.
    Falls back to phrase/keyword decide_discovery when no RelevanceClient is configured.
    Enforces that absence is not evidence: insufficient coverage blocks CANDIDATE verdict.
    """
    from app.agents.market_filter import decide_discovery

    # Union of (a) prior_rows and (b) problem_alternatives
    combined_priors: list[dict[str, Any]] = []
    seen_ids: dict[str, dict[str, Any]] = {}

    for r in list(prior_rows or []) + list(problem_alternatives or []):
        alt_id = str(r.get("alternative_id") or r.get("url") or "").strip()
        alt_key = alt_id.casefold()
        if not alt_key:
            continue
        if alt_key in seen_ids:
            existing = seen_ids[alt_key]
            if r.get("overlap") and not existing.get("overlap"):
                existing["overlap"] = r["overlap"]
            if r.get("same_problem") is not None and existing.get("same_problem") is None:
                existing["same_problem"] = r["same_problem"]
            if r.get("excerpt") and not existing.get("excerpt"):
                existing["excerpt"] = r["excerpt"]
            if r.get("description") and not existing.get("description"):
                existing["description"] = r["description"]
            if r.get("readme") and not existing.get("readme"):
                existing["readme"] = r["readme"]
            continue
        seen_ids[alt_key] = r
        combined_priors.append(r)

    from app.agents.own_identity import build_identity, names_own_identity, own_reason

    identity = build_identity(candidate)
    combined_priors = [row for row in combined_priors if not own_reason(identity, row)]

    # Check multi-source coverage guards
    has_source_error = source_status is not None and any(st == "SOURCE_ERROR" for st in source_status.values())
    budget_cut = sorted(s for s, st in (source_status or {}).items() if st == "BUDGET_EXHAUSTED")
    zero_results = source_counts is not None and sum(source_counts.values()) == 0 and len(combined_priors) == 0
    fewer_than_two = source_status is not None and len(source_status) >= 2 and sum(1 for st in source_status.values() if st == "ok") < 2 and len(combined_priors) == 0
    coverage_blocked = (insufficient_coverage and len(combined_priors) == 0) or has_source_error or bool(budget_cut) or zero_results or fewer_than_two

    if has_source_error:
        err_sources = [s for s, st in source_status.items() if st == "SOURCE_ERROR"]
        coverage_reason = f"INSUFFICIENT_PRIOR_ART_COVERAGE: source error in {', '.join(err_sources)}"
    elif budget_cut:
        coverage_reason = (
            f"INSUFFICIENT_PRIOR_ART_COVERAGE: tool budget exhausted before the search finished ({', '.join(budget_cut)}); "
            "an unfinished search is not evidence of no competition"
        )
    elif zero_results:
        coverage_reason = "INSUFFICIENT_PRIOR_ART_COVERAGE: zero alternatives found across all sources"
    elif fewer_than_two:
        coverage_reason = "INSUFFICIENT_PRIOR_ART_COVERAGE: fewer than 2 sources succeeded"
    else:
        coverage_reason = "INSUFFICIENT_PRIOR_ART_COVERAGE: insufficient prior art coverage"

    def _is_web_row(r: dict[str, Any]) -> bool:
        return str(r.get("source") or "") == "web" or str(r.get("alternative_id") or "").startswith("web:")

    mainstream_overlap = [
        r for r in combined_priors
        if _is_web_row(r) and r.get("same_problem") is True and str(r.get("overlap") or "") in {"partial", "full"}
    ]
    # A verified full-overlap alternative of any source (GitHub included) means an open-source or
    # commercial substitute exists; GitHub partial overlap only needs a human look.
    full_overlap_rows = [
        r for r in combined_priors
        if r.get("same_problem") is True and str(r.get("overlap") or "") == "full"
    ]
    github_partial_rows = [
        r for r in combined_priors
        if not _is_web_row(r) and r.get("same_problem") is True and str(r.get("overlap") or "") == "partial"
    ]
    # Show same-problem alternatives (web first) to the judge before unrelated rows.
    combined_priors.sort(key=lambda r: (r.get("same_problem") is not True, not _is_web_row(r)))

    if not is_client_configured(client):
        verdict, reason = decide_discovery(candidate, combined_priors, demand_rows)
        clean_reason = reason.replace("Method: phrase/keyword.", "").rstrip().rstrip(".")
        return verdict, f"{clean_reason}. Method: phrase/keyword (fallback)."

    if client is None:
        client = RelevanceClient()

    repo_id = str(candidate.get("repo_id") or "").strip()
    readme = str(candidate.get("readme_excerpt") or "").strip()
    desc = str(candidate.get("description") or "").strip()
    prob = str(candidate.get("problem_excerpt") or "").strip()
    cand_text = f"{desc}\n{readme}\n{prob}".strip()

    prompt_record = load_prompt("relevance_discovery")

    user_prompt_lines = [
        f"Candidate Repository: {repo_id}",
        f"Description: {desc}",
        f"README Excerpt:\n```\n{readme}\n```",
    ]
    if prob:
        user_prompt_lines.append(f"Problem Excerpt: {prob}")

    if combined_priors:
        priors_desc = []
        for r in combined_priors[:10]:
            alt_id = r.get("alternative_id") or r.get("url")
            lic = r.get("licence_spdx") or "unknown"
            exc = (r.get("excerpt") or r.get("description") or r.get("text") or "")[:200]
            priors_desc.append(f"- Alternative: {alt_id} (licence: {lic})\n  Excerpt: {exc}")
        user_prompt_lines.append("Prior Art Alternatives Checked:\n" + "\n".join(priors_desc))
    else:
        user_prompt_lines.append("Prior Art Alternatives Checked: None")

    if demand_rows:
        demands_desc = []
        for d in demand_rows[:5]:
            url = d.get("url") or d.get("signal_id")
            exc = (d.get("excerpt") or d.get("text") or "")[:200]
            demands_desc.append(f"- Demand Signal: {url}\n  Excerpt: {exc}")
        user_prompt_lines.append("External Demand Signals:\n" + "\n".join(demands_desc))
    else:
        user_prompt_lines.append("External Demand Signals: None")

    user_prompt_lines.append(
        "Judge whether this candidate is CANDIDATE, ALREADY_EXISTS, or NO_GO according to the rubric. Output strict JSON."
    )
    user_prompt = "\n\n".join(user_prompt_lines)

    # Retry an unusable answer (and drop it from the cache, so it is never replayed): one malformed
    # reply must not turn a repo into NEEDS_REVIEW for every later run.
    obj, text, req_m, srv_m = _complete_json(
        client, user_prompt, prompt_record.text, repo_id, "discovery",
        validate=lambda o: "verdict" in o,
    )
    if obj is None:
        verdict = "NEEDS_REVIEW"
        reason = "unverified LLM quote: LLM response was not valid JSON"
        return verdict, f"{reason}. Method: llm+verified quote (requested={req_m}, served={srv_m})."

    raw_verdict = str(obj.get("verdict") or "NO_GO").strip().upper()
    reason = str(obj.get("reason") or "").strip()
    problem_quote = str(obj.get("problem_quote") or "").strip()
    alt_repo = str(obj.get("alternative_repo") or "").strip() if obj.get("alternative_repo") else None
    alt_quote = str(obj.get("alternative_quote") or "").strip() if obj.get("alternative_quote") else None

    # Deterministic verification. `picked` is what the report shows for a refusal or a review.
    picked: list[dict[str, Any]] = []
    from datetime import date as _date

    from app.agents.blocker import evaluate_blocker

    must_beat: list[dict[str, str]] = []

    def _blocker(row: dict[str, Any]) -> dict[str, Any]:
        return evaluate_blocker(candidate, row, as_of=_date.today())

    def _must(row: dict[str, Any], decision: dict[str, Any]) -> dict[str, str]:
        quotes = [str(q).strip() for q in (row.get("quotes") or []) if str(q).strip()]
        return {
            "alternative": str(row.get("alternative_id") or row.get("url") or ""),
            "overlap": str(row.get("overlap") or ""),
            "quote": quotes[0] if quotes else "",
            "reason": "did not block: " + "; ".join(decision.get("reasons") or []),
        }

    blocking_full: list[dict[str, Any]] = []
    for row in full_overlap_rows:
        decision = _blocker(row)
        if decision["blocks"]:
            blocking_full.append(row)
        else:
            must_beat.append(_must(row, decision))

    if has_source_error and raw_verdict != "ALREADY_EXISTS":
        verdict = "NEEDS_REVIEW"
        reason = coverage_reason
    elif zero_results:
        verdict = "NEEDS_REVIEW"
        reason = coverage_reason
    elif raw_verdict == "CANDIDATE" and blocking_full:
        # The LLM called it novel, but a verified full-overlap alternative also passes the
        # maturity rule. A quote is still required: it was checked when the alternative was judged.
        m = blocking_full[0]
        quotes = [str(q) for q in (m.get("quotes") or []) if str(q).strip()]
        verdict = "ALREADY_EXISTS"
        reason = (
            f"verified full-overlap alternative {m.get('url') or m.get('alternative_id')} already solves the same "
            f"problem" + (f" (quote: '{quotes[0]}')" if quotes else "") + "; overrides LLM CANDIDATE"
        )
        picked = [{**m, "quotes": quotes, "overlap": "full", "reason": reason}]
    elif raw_verdict == "CANDIDATE":
        if coverage_blocked:
            verdict = "NEEDS_REVIEW"
            reason = coverage_reason
        elif not problem_quote or not quote_in_text(problem_quote, cand_text):
            verdict = "NEEDS_REVIEW"
            reason = f"unverified LLM quote: problem_quote '{problem_quote}' not found in candidate README or description"
        elif github_partial_rows and not mainstream_overlap and _blocker(github_partial_rows[0])["role"] == "mature_partial":
            m = github_partial_rows[0]
            verdict = "NEEDS_REVIEW"
            reason = (
                f"ALTERNATIVE_OVERLAP: repo {m.get('url') or m.get('alternative_id')} solves the same problem "
                f"(overlap partial); a human must confirm the remaining gap before CANDIDATE"
            )
            picked = [{**m, "reason": reason}]
        elif mainstream_overlap and _blocker(mainstream_overlap[0])["role"] == "mature_partial":
            # A maintained official or mature product on the web solves the same problem in part.
            m = mainstream_overlap[0]
            verdict = "NEEDS_REVIEW"
            reason = (
                f"MAINSTREAM_OVERLAP: web result {m.get('url') or m.get('alternative_id')} solves the same problem "
                f"(overlap {m.get('overlap')}); a human must confirm the remaining gap before CANDIDATE"
            )
            picked = [{**m, "reason": reason}]
        else:
            for row in list(github_partial_rows) + list(mainstream_overlap):
                decision = _blocker(row)
                if decision["role"] != "mature_partial":
                    must_beat.append(_must(row, decision))
            verdict = "CANDIDATE"
    elif raw_verdict == "ALREADY_EXISTS" and names_own_identity(identity, alt_repo or ""):
        # The model cited this repository's own package, docs, or mirror. That is not a blocker.
        if problem_quote and quote_in_text(problem_quote, cand_text):
            verdict = "CANDIDATE"
            reason = f"ignored own identity {alt_repo}"
        else:
            verdict = "NEEDS_REVIEW"
            reason = f"unverified LLM quote: problem_quote '{problem_quote}' not found in candidate README or description"
    elif raw_verdict == "ALREADY_EXISTS":
        # Check alternative repo matches one in combined_priors
        matched_row = None
        if alt_repo:
            for r in combined_priors:
                r_id = str(r.get("alternative_id") or "").strip()
                r_name = r_id.split("/")[-1] if "/" in r_id else r_id
                r_url = str(r.get("url") or "").strip()
                r_desc = str(r.get("description") or "").strip()
                if (
                    alt_repo.casefold() == r_id.casefold()
                    or alt_repo.casefold() == r_name.casefold()
                    or alt_repo.casefold() in r_id.casefold()
                    or r_id.casefold() in alt_repo.casefold()
                    or (r_url and alt_repo.casefold() in r_url.casefold())
                    or (r_desc and alt_repo.casefold() in r_desc.casefold())
                ):
                    matched_row = r
                    break
        if matched_row is None:
            verdict = "NEEDS_REVIEW"
            reason = f"unverified LLM quote: alternative_repo '{alt_repo}' not found in candidate prior-art"
        else:
            alt_text = f"{matched_row.get('excerpt', '')} {matched_row.get('description', '')} {matched_row.get('text', '')} {matched_row.get('readme', '')}"
            if not alt_quote or not quote_in_text(alt_quote, alt_text):
                verdict = "NEEDS_REVIEW"
                reason = f"unverified LLM quote: alternative_quote '{alt_quote}' not found in alternative {matched_row.get('alternative_id')} text"
                picked = [{**matched_row, "reason": reason}]
            elif matched_row.get("overlap") and matched_row.get("overlap") != "full":
                verdict = "NEEDS_REVIEW"
                reason = f"unverified ALREADY_EXISTS: alternative '{matched_row.get('alternative_id')}' has overlap '{matched_row.get('overlap')}', requires verified full overlap"
                picked = [{**matched_row, "quotes": [alt_quote] if alt_quote else [], "reason": reason}]
            elif any(r.get("overlap") for r in combined_priors) and not any(r.get("overlap") == "full" for r in combined_priors) and matched_row.get("overlap") != "full" and not matched_row.get("licence_spdx"):
                verdict = "NEEDS_REVIEW"
                reason = "unverified ALREADY_EXISTS: requires at least one verified full-overlap alternative"
                picked = [{**matched_row, "quotes": [alt_quote] if alt_quote else [], "reason": reason}]
            else:
                named = {
                    **matched_row,
                    "overlap": matched_row.get("overlap") or "full",
                    "same_problem": True if matched_row.get("same_problem") is None else matched_row.get("same_problem"),
                    "quotes": [alt_quote] if alt_quote else list(matched_row.get("quotes") or []),
                }
                # The discovery judge already called this the same product. A-E still have to pass.
                if named.get("same_job") is None and named.get("overlap") == "full":
                    named["same_job"] = True
                decision = _blocker(named)
                if decision["blocks"]:
                    verdict = "ALREADY_EXISTS"
                    picked = [{**named, "reason": reason}]
                elif blocking_full:
                    m = blocking_full[0]
                    quotes = [str(q) for q in (m.get("quotes") or []) if str(q).strip()]
                    verdict = "ALREADY_EXISTS"
                    reason = (
                        f"verified full-overlap alternative {m.get('url') or m.get('alternative_id')} already solves the same problem"
                        + (f" (quote: '{quotes[0]}')" if quotes else "")
                    )
                    picked = [{**m, "quotes": quotes, "overlap": "full", "reason": reason}]
                else:
                    must_beat.append(_must(named, decision))
                    why = "did not block: " + "; ".join(decision.get("reasons") or [])
                    if problem_quote and quote_in_text(problem_quote, cand_text):
                        verdict = "CANDIDATE"
                        reason = f"must beat {named.get('alternative_id')}: {why}"
                    else:
                        verdict = "NEEDS_REVIEW"
                        reason = f"must beat {named.get('alternative_id')}: {why}"
                        picked = [{**named, "reason": reason}]
    elif raw_verdict == "NO_GO":
        if problem_quote and problem_quote.casefold() not in {"unknown", "none", "null", ""}:
            if not quote_in_text(problem_quote, cand_text):
                verdict = "NEEDS_REVIEW"
                reason = f"unverified LLM quote: problem_quote '{problem_quote}' not found in candidate README or description"
            else:
                verdict = "NO_GO"
        else:
            verdict = "NO_GO"
    else:
        verdict = "NEEDS_REVIEW"
        reason = f"unverified LLM quote: unexpected verdict '{raw_verdict}'"

    clean_reason = reason.rstrip().rstrip(".")
    labeled_reason = f"{clean_reason}. Method: llm+verified quote (requested={req_m}, served={srv_m})."
    show = _evidence_rows(picked) if verdict in {"ALREADY_EXISTS", "NEEDS_REVIEW"} else []
    blocked_names = {
        str(p.get("alternative_id") or p.get("alternative") or p.get("url") or "").casefold()
        for p in picked
    }
    deduped: list[dict[str, str]] = []
    seen_beat: set[str] = set()
    for item in must_beat:
        key = str(item.get("alternative") or "").casefold()
        if not key or key in seen_beat or key in blocked_names:
            continue
        seen_beat.add(key)
        deduped.append(item)
    return DiscoveryDecision(verdict, labeled_reason, show, deduped)


def extract_problem_statement(
    candidate: dict[str, Any],
    client: RelevanceClient | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Extract candidate problem statement, target user, category, search queries, and supporting quotes.

    Verifies every quote verbatim in candidate README/description.
    Returns (problem_data, metadata). If verification fails or JSON invalid, problem_data is None.
    """
    repo_id = str(candidate.get("repo_id") or "").strip()
    readme = str(candidate.get("readme_excerpt") or "").strip()
    desc = str(candidate.get("description") or "").strip()
    prob = str(candidate.get("problem_excerpt") or "").strip()
    cand_text = f"{desc}\n{readme}\n{prob}".strip()

    if not cand_text:
        return None, {"error": "Candidate text is empty", "fallback": False}

    if client is None:
        client = RelevanceClient()

    prompt_record = load_prompt("relevance_problem")
    user_prompt = (
        f"Candidate Repository: {repo_id}\n"
        f"Description: {desc}\n"
        f"README Excerpt:\n```\n{readme}\n```\n\n"
        "Extract the problem statement, target user, category, 3-5 problem-oriented search queries, "
        "and verbatim supporting quotes from the candidate README as strict JSON."
    )

    try:
        obj, text, req_m, srv_m = _complete_json(
            client, user_prompt, prompt_record.text, repo_id, "problem"
        )
    except Exception as exc:
        if isinstance(exc, RelevanceLLMError) and "refusing to score with a small local model" in str(exc):
            raise
        return None, {
            "requested_model": getattr(client, "model", DEFAULT_NIGHTSHIFT_MODEL),
            "served_model": "",
            "fallback": True,
            "error": str(exc),
        }

    if obj is None:
        return None, {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": True,
            "error": "LLM output invalid JSON",
        }

    quotes = obj.get("quotes")
    if not isinstance(quotes, list) or len(quotes) == 0:
        return None, {
            "error": "unverified LLM quote: no quotes provided in problem statement",
            "unverified_quote": "",
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": False,
        }

    for q in quotes:
        q_str = str(q or "").strip()
        if not q_str or not quote_in_text(q_str, cand_text):
            return None, {
                "error": f"unverified LLM quote: problem quote '{q_str}' not found in candidate README or description",
                "unverified_quote": q_str,
                "requested_model": req_m,
                "served_model": srv_m,
                "fallback": False,
            }

    raw_queries = obj.get("search_queries") or []
    search_queries = [str(sq).strip() for sq in raw_queries if str(sq).strip()]

    problem_data = {
        "problem": str(obj.get("problem") or "").strip(),
        "target_user": str(obj.get("target_user") or "").strip(),
        "category": str(obj.get("category") or "").strip(),
        "search_queries": search_queries[:5],
        "quotes": [str(q).strip() for q in quotes if str(q).strip()],
    }
    return problem_data, {"requested_model": req_m, "served_model": srv_m, "fallback": False}


class SearchResultsList(list):
    """List of candidate alternative dicts with multi-source coverage metadata."""

    def __init__(
        self,
        items: list[dict[str, Any]] | None = None,
        source_counts: dict[str, int] | None = None,
        source_status: dict[str, str] | None = None,
        insufficient_coverage: bool = False,
    ) -> None:
        super().__init__(items or [])
        self.source_counts: dict[str, int] = source_counts or {}
        self.source_status: dict[str, str] = source_status or {}
        self.insufficient_coverage: bool = insufficient_coverage


def search_prior_art(
    candidate: dict[str, Any],
    queries: list[str],
    toolbox: Any,
    language: str | None = None,
) -> SearchResultsList:
    """Search for potential prior-art alternatives across queries via the existing toolbox.

    Queries GitHub, npm and the web (Exa, Parallel, Keenable, DuckDuckGo). PyPI search is not
    counted as a source (its search page is behind a bot challenge).
    Deduplicates results, excludes candidate itself and its forks, keeps at most 15 results.
    ``language`` is accepted for compatibility and deliberately not used to filter: prior art
    written in another language still solves the same problem.
    Never uses candidate's own stars or forks anywhere.
    Tracks per-source counts and status.
    """
    from app.agents.own_identity import build_identity, own_reason

    cand_id = str(candidate.get("repo_id") or "").strip()
    cand_name = cand_id.split("/")[-1].casefold() if "/" in cand_id else cand_id.casefold()
    cand_url = str(candidate.get("html_url") or "").strip().casefold().rstrip("/")
    identity = build_identity(candidate)
    own_filtered: list[dict[str, str]] = []

    def _own(row: dict[str, Any], why: str) -> None:
        own_filtered.append({
            "alternative": str(row.get("alternative_id") or row.get("full_name") or row.get("name") or ""),
            "url": str(row.get("url") or row.get("html_url") or ""),
            "reason": why,
        })

    results: list[dict[str, Any]] = []
    seen_ids: set[str] = {cand_id.casefold()}
    if cand_url:
        seen_ids.add(cand_url)

    # Detect active sources in toolbox
    active_sources: set[str] = set()
    if toolbox is not None:
        if hasattr(toolbox, "has"):
            for s in ["github", "npm", "pypi", "web_search", "web"]:
                if toolbox.has(s):
                    active_sources.add(s)
        if hasattr(toolbox, "tools") and isinstance(toolbox.tools, dict):
            for s in ["github", "npm", "pypi", "web_search", "web"]:
                if s in toolbox.tools:
                    active_sources.add(s)
    if not active_sources:
        active_sources = {"github", "npm", "pypi", "web"}

    source_counts: dict[str, int] = {"github": 0, "npm": 0, "pypi": 0, "web": 0}
    source_status: dict[str, str] = {}
    for s in ["github", "npm", "pypi", "web"]:
        if s in active_sources or (s == "web" and "web_search" in active_sources):
            source_status[s] = "ok"

    # Collect one bucket per query, then merge round-robin so the first query
    # cannot fill every slot (e.g. a tech-stack query crowding out the job query).
    # 15 is the documented cap. It was 18 and then cut to 15 on return, so source_counts
    # counted rows that were dropped and README calls were spent on them.
    max_results = 15
    buckets: list[list[tuple[str, dict[str, Any]]]] = []
    if "pypi" in source_status:
        # PyPI's search page is behind a bot challenge; package metadata lookups still work,
        # but search is not a reliable source, so it is not counted as a search source.
        source_status.pop("pypi", None)

    # Sources whose planned queries were skipped because the tool budget ran out. A search cut
    # short is not a search that found no competitor, so it must block CANDIDATE like a source
    # error does. (The budget clock starts before problem extraction, and a slow Nightshift can
    # spend most of it before the search begins.)
    truncated: set[str] = set()
    searched_queries = 0

    def _plan(index: int) -> set[str]:
        planned = {src for src in ("github", "web") if src in source_status}
        if "npm" in source_status and index < 3:
            planned.add("npm")
        return planned

    for idx, q in enumerate(queries):
        if toolbox is None:
            break
        if toolbox.budget.is_exhausted():
            for rest in range(idx, len(queries)):
                truncated |= _plan(rest)
            break
        searched_queries += 1
        bucket: list[tuple[str, dict[str, Any]]] = []

        # 1. GitHub repositories (every query; README fetched later for selected rows only)
        if "github" in source_status:
            gh_res = toolbox.call("github", {"action": "repositories", "query": q, "per_page": 5})
            if gh_res.ok and isinstance(gh_res.data, dict):
                for it in gh_res.data.get("items") or []:
                    full_name = str(it.get("full_name") or it.get("name") or "").strip()
                    if not full_name:
                        continue
                    fn_lower = full_name.casefold()
                    if fn_lower == cand_id.casefold():
                        _own(it, "same repository")
                        continue
                    is_fork = bool(it.get("fork")) or bool(it.get("is_fork"))
                    parent_fn = str((it.get("parent") or {}).get("full_name") or "").casefold()
                    source_fn = str((it.get("source") or {}).get("full_name") or "").casefold()
                    it_name = str(it.get("name") or "").casefold()
                    it_desc = str(it.get("description") or "").casefold()
                    if parent_fn == cand_id.casefold() or source_fn == cand_id.casefold():
                        _own(it, "fork of this repository")
                        continue
                    if is_fork and (it_name == cand_name or f"forked from {cand_id.casefold()}" in it_desc):
                        _own(it, "fork of this repository")
                        continue
                    if it.get("is_fork_of_candidate"):
                        _own(it, "fork of this repository")
                        continue
                    lic = it.get("license") or {}
                    spdx = str(lic.get("spdx_id") or "") if isinstance(lic, dict) else str(lic or "")
                    gh_row = {
                        "alternative_id": full_name,
                        "url": str(it.get("html_url") or it.get("url") or f"https://github.com/{full_name}").strip(),
                        "description": str(it.get("description") or "").strip(),
                        "readme": str(it.get("readme") or ""),
                        "licence_spdx": spdx,
                        "stars": it.get("stargazers_count", 0),
                        "pushed_at": str(it.get("pushed_at") or ""),
                        "source": "github",
                        "query": q,
                        "fork": is_fork,
                        "parent": it.get("parent") if isinstance(it.get("parent"), dict) else {},
                    }
                    why_own = own_reason(identity, gh_row)
                    if why_own:
                        _own(gh_row, why_own)
                        continue
                    bucket.append((fn_lower, gh_row))
            elif not gh_res.ok and str(gh_res.error or "") == "BUDGET_EXHAUSTED":
                truncated.add("github")
            elif not gh_res.ok:
                # A rate limit (HTTP 403/429), an HTTP error or a network error is a source error,
                # the same as for npm and web. It used to count only when the message contained
                # the literal "SOURCE_ERROR", so a rate-limited GitHub still counted as a
                # successful source and coverage looked sufficient.
                source_status["github"] = "SOURCE_ERROR"

        # 2. npm registry (first 3 queries)
        if "npm" in source_status and idx < 3 and toolbox.budget.is_exhausted():
            truncated.add("npm")
        elif "npm" in source_status and idx < 3:
            npm_res = toolbox.call("npm", {"action": "search", "query": q})
            if npm_res.ok and isinstance(npm_res.data, dict):
                for it in (npm_res.data.get("items") or [])[:5]:
                    pkg_name = str(it.get("name") or "").strip()
                    if not pkg_name or pkg_name.casefold() == cand_name:
                        if pkg_name:
                            _own({"alternative_id": f"npm:{pkg_name}", "url": it.get("url") or ""}, "own registry package")
                        continue
                    alt_id = f"npm:{pkg_name}"
                    npm_row = {
                        "alternative_id": alt_id,
                        "url": it.get("url") or f"https://www.npmjs.com/package/{pkg_name}",
                        "description": str(it.get("description") or ""),
                        "readme": "",
                        "licence_spdx": str(it.get("license") or ""),
                        "stars": 0,
                        "pushed_at": "",
                        "source": "npm",
                        "query": q,
                    }
                    why_own = own_reason(identity, npm_row)
                    if why_own:
                        _own(npm_row, why_own)
                        continue
                    bucket.append((alt_id.casefold(), npm_row))
            elif not npm_res.ok and str(npm_res.error or "") == "BUDGET_EXHAUSTED":
                truncated.add("npm")
            elif not npm_res.ok:
                source_status["npm"] = "SOURCE_ERROR"

        # 3. Web (Exa first, every query) - surfaces official features, docs and commercial products
        if "web" in source_status and toolbox.budget.is_exhausted():
            truncated.add("web")
        elif "web" in source_status:
            tool_name = "web_search" if (hasattr(toolbox, "has") and toolbox.has("web_search")) or (hasattr(toolbox, "tools") and "web_search" in toolbox.tools) else "web"
            web_res = toolbox.call(tool_name, {"query": q})
            if web_res.ok and isinstance(web_res.data, dict):
                for it in (web_res.data.get("items") or [])[:6]:
                    url = str(it.get("url") or "").strip()
                    title = str(it.get("title") or "").strip()
                    snippet = str(it.get("snippet") or "").strip()
                    if not url:
                        continue
                    url_lower = url.casefold().rstrip("/")
                    web_row = {
                        "alternative_id": f"web:{url}",
                        "url": url,
                        "description": f"{title} - {snippet}" if title and snippet else (title or snippet),
                        "readme": snippet,
                    }
                    why = own_reason(identity, web_row)
                    if (cand_url and cand_url in url_lower) or cand_id.casefold() in url_lower or why:
                        _own(web_row, why or "own repository URL")
                        continue
                    desc = f"{title} - {snippet}" if title and snippet else (title or snippet)
                    bucket.append((url_lower, {
                        "alternative_id": f"web:{url}",
                        "url": url,
                        "description": desc,
                        "readme": snippet,
                        "licence_spdx": "",
                        "stars": 0,
                        "pushed_at": "",
                        "source": "web",
                        "query": q,
                    }))
            elif not web_res.ok and str(web_res.error or "") == "BUDGET_EXHAUSTED":
                truncated.add("web")
            elif not web_res.ok:
                source_status["web"] = "SOURCE_ERROR"

        # One bucket per (query, source) so GitHub rows cannot crowd out npm and web rows.
        for src in ("github", "npm", "web"):
            buckets.append([x for x in bucket if x[1]["source"] == src])

    # Merge: round-robin across queries within each source, then per-source quotas
    # so GitHub/npm rows cannot crowd out web rows (official features and commercial
    # products), then fill any unused slots from what is left.
    quotas = {"web": 9, "github": 6, "npm": 3}
    per_source: dict[str, list[tuple[str, dict[str, Any]]]] = {"github": [], "npm": [], "web": []}
    for src in ("github", "npm", "web"):
        src_buckets = [bk for bk in buckets if bk and bk[0][1]["source"] == src]
        cur = [0] * len(src_buckets)
        moved = True
        while moved:
            moved = False
            for bi, bk in enumerate(src_buckets):
                if cur[bi] < len(bk):
                    per_source[src].append(bk[cur[bi]])
                    cur[bi] += 1
                    moved = True
    leftovers: list[tuple[str, dict[str, Any]]] = []
    for src in ("web", "github", "npm"):
        taken = 0
        for key, row in per_source[src]:
            if key in seen_ids:
                continue
            if taken < quotas[src] and len(results) < max_results:
                seen_ids.add(key)
                results.append(row)
                source_counts[row["source"]] = source_counts.get(row["source"], 0) + 1
                taken += 1
            else:
                leftovers.append((key, row))
    for key, row in leftovers:
        if len(results) >= max_results:
            break
        if key in seen_ids:
            continue
        seen_ids.add(key)
        results.append(row)
        source_counts[row["source"]] = source_counts.get(row["source"], 0) + 1

    # README excerpts for selected GitHub rows only
    for row in results:
        if row["source"] != "github" or row["readme"] or toolbox is None:
            continue
        if toolbox.budget.is_exhausted() or toolbox.budget.remaining_calls <= 1:
            break
        rm_res = toolbox.call("github", {"action": "readme", "repo": row["alternative_id"]})
        if rm_res.ok and isinstance(rm_res.data, dict):
            row["readme"] = str(rm_res.data.get("decoded_text") or "")[:2000]

    # A source that skipped planned queries for lack of budget did not finish its search.
    for src in sorted(truncated):
        if source_status.get(src) == "ok":
            source_status[src] = "BUDGET_EXHAUSTED"

    # Compute coverage status
    successful_sources = sum(1 for st in source_status.values() if st == "ok")
    has_source_error = any(st == "SOURCE_ERROR" for st in source_status.values())
    zero_found = len(results) == 0

    if len(source_status) >= 2:
        insufficient_coverage = zero_found or has_source_error or (successful_sources < 2)
    else:
        insufficient_coverage = zero_found or has_source_error
    if any(st == "BUDGET_EXHAUSTED" for st in source_status.values()):
        insufficient_coverage = True

    found = SearchResultsList(
        results[:15],
        source_counts=source_counts,
        source_status=source_status,
        insufficient_coverage=insufficient_coverage,
    )
    found.own_filtered = own_filtered
    found.searched_queries = searched_queries
    found.planned_queries = len(queries)
    return found


def judge_alternative_relevance(
    problem_statement: dict[str, Any],
    candidate_repo_id: str,
    search_result: dict[str, Any],
    client: RelevanceClient | None = None,
    trace: Any | None = None,
    call_budget_s: float | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Judge if a search result solves the same problem as the candidate (step="alternative").

    Verifies quotes verbatim against result description/README.
    Unverified results are dropped and recorded in trace.
    """
    alt_id = str(search_result.get("alternative_id") or "").strip()
    alt_desc = str(search_result.get("description") or "").strip()
    alt_readme = str(search_result.get("readme") or "").strip()
    result_text = f"{alt_desc}\n{alt_readme}".strip()

    if not result_text:
        return None, {"error": "Alternative text is empty", "dropped": True}

    if client is None:
        client = RelevanceClient()

    prompt_record = load_prompt("relevance_alternative")
    user_prompt = (
        f"Candidate Problem: {problem_statement.get('problem', '')}\n"
        f"Target User: {problem_statement.get('target_user', '')}\n"
        f"Category: {problem_statement.get('category', '')}\n\n"
        f"Alternative Repository: {alt_id}\n"
        f"URL: {search_result.get('url', '')}\n"
        f"Description and README Excerpt:\n```\n{result_text}\n```\n\n"
        "Judge whether this alternative solves the same core user problem, the overlap degree (full, partial, none), "
        "and provide verbatim supporting quotes as strict JSON."
    )

    try:
        text, req_m, srv_m = client.complete(
            user_prompt, prompt_record.text, repo_id=candidate_repo_id, step="alternative",
            call_budget_s=call_budget_s,
        )
    except Exception as exc:
        if isinstance(exc, RelevanceLLMError) and "refusing to score with a small local model" in str(exc):
            raise
        return None, {
            "requested_model": getattr(client, "model", DEFAULT_NIGHTSHIFT_MODEL),
            "served_model": "",
            "fallback": True,
            "error": str(exc),
            "dropped": True,
        }

    obj = extract_json_object(text)
    if obj is None:
        return None, {
            "requested_model": req_m,
            "served_model": srv_m,
            "fallback": True,
            "error": "LLM output invalid JSON",
            "dropped": True,
        }

    same_problem = bool(obj.get("same_problem"))
    overlap = str(obj.get("overlap") or "none").strip().lower()
    reason = str(obj.get("reason") or "").strip()
    quotes = obj.get("quotes") or []

    # Verify quotes verbatim
    if same_problem:
        if not isinstance(quotes, list) or len(quotes) == 0:
            if trace is not None:
                trace.event(
                    agent_name="orchestrator",
                    action="UNVERIFIED_ALTERNATIVE_QUOTE",
                    input_summary={"alternative": alt_id},
                    output_summary={"error": "no quotes provided for same_problem alternative"},
                    status="error",
                )
            return None, {"error": "unverified LLM quote: no quotes provided", "dropped": True}

        for q in quotes:
            q_str = str(q or "").strip()
            if not q_str or not quote_in_text(q_str, result_text):
                if trace is not None:
                    trace.event(
                        agent_name="orchestrator",
                        action="UNVERIFIED_ALTERNATIVE_QUOTE",
                        input_summary={"alternative": alt_id, "quote": q_str},
                        output_summary={"error": f"unverified quote '{q_str}' not found in alternative text"},
                        status="error",
                    )
                return None, {"error": f"unverified LLM quote: '{q_str}' not found in alternative text", "dropped": True}

    if not same_problem:
        return None, {
            "same_problem": False,
            "overlap": overlap,
            "reason": reason,
            "requested_model": req_m,
            "served_model": srv_m,
        }

    alt_row = {
        "repo_id": candidate_repo_id,
        "alternative_id": alt_id,
        "url": str(search_result.get("url") or f"https://github.com/{alt_id}"),
        "licence_spdx": str(search_result.get("licence_spdx") or ""),
        "stars": search_result.get("stars", 0),
        "pushed_at": str(search_result.get("pushed_at") or ""),
        "excerpt": result_text[:1000],
        "candidate_excerpt": str(problem_statement.get("problem") or ""),
        "required_terms": "",
        "same_problem": True,
        "overlap": overlap,
        "reason": reason,
        "quotes": [str(q).strip() for q in quotes if str(q).strip()],
        "quote_verified": True,
        "source": str(search_result.get("source") or ""),
        "archived": search_result.get("archived", False),
    }
    return alt_row, {
        "same_problem": True,
        "overlap": overlap,
        "reason": reason,
        "requested_model": req_m,
        "served_model": srv_m,
    }


BATCH_INSTRUCTION = (
    "\n\nBATCH MODE: you will receive several numbered alternatives. Judge EACH one independently "
    "with the rules above. Output strict JSON only, in the form "
    '{"results": [{"index": <number>, "same_problem": true|false, "overlap": "full"|"partial"|"none", '
    '"reason": "...", "quotes": ["verbatim quote from THAT alternative\'s text"]}]} '
    "with exactly one entry per alternative index. Use the index numbers shown in brackets. Do not renumber."
)

# Why a chunk used to fall back to one call per alternative (42 times on one 30-repo run):
# 1. Chunks were 5 items, each up to 900 characters, and Nightshift replies often open with a
#    reasoning preamble. This client caps the reply at max_tokens 4096 and the call at a 120s
#    wall clock, so the JSON was truncated or the call returned HTTP 502 (calls took 7-280s).
# 2. The reply was kept only when every index was present. A cut-off array, or one omitted
#    index, threw away the objects that had already parsed.
# 3. The caller then re-judged every alternative in the chunk on its own.
# Chunks of 2 fit a single reply. A partial reply keeps every object that closed, and only the
# indexes that did not parse are retried. The per-repo alternatives budget, not a 120s cap,
# bounds how long those calls may run. A finished chunk is cached with the requested model.
BATCH_CALL_ATTEMPTS = 2
ALTERNATIVE_CHUNK_SIZE = 2
_BATCH_RETRY_NOTE = (
    "\n\n(Retry: your previous reply could not be used. Reply with ONLY one strict JSON object "
    "of the form {\"results\": [{\"index\": <number>, \"same_problem\": true|false, "
    "\"overlap\": \"full\"|\"partial\"|\"none\", \"reason\": \"...\", \"quotes\": [\"...\"]}]}. "
    "No prose and no code fences.)"
)


def _trace_llm(
    trace: Any | None,
    action: str,
    repo_id: str,
    requested: str,
    served: str,
    info: dict[str, Any],
    status: str = "ok",
    record_model: bool = True,
) -> None:
    """One trace line per model call. served_model is a top-level field, inside the hash chain.

    record_model is false when RelevanceClient.complete already wrote the LLM_CALL line
    for this attempt. The batch outcome line then stays, without a second model flag.
    """
    if trace is None:
        return
    kwargs: dict[str, Any] = {}
    if record_model:
        kwargs["llm"] = {"requested_model": requested or "", "served_model": served or ""}
    summary = {
        "requested_model": requested or "",
        "served_model": served or "",
        "parsed_indexes": info.get("parsed_indexes"),
        "reason": info.get("reason"),
    }
    if "quote_verified" in info:
        summary["overlap"] = info.get("overlap")
        summary["same_problem"] = info.get("same_problem")
        summary["quote_verified"] = info.get("quote_verified")
    if info.get("chunk_index") is not None:
        summary["chunk_index"] = info.get("chunk_index")
    trace.event(
        agent_name="orchestrator",
        action=action,
        input_summary={
            "repo_id": repo_id,
            "indexes": info.get("indexes"),
            "alternative": info.get("alternative"),
            **({"chunk_index": info.get("chunk_index")} if info.get("chunk_index") is not None else {}),
        },
        output_summary=summary,
        status=status,
        **kwargs,
    )


def _batch_prompt(problem_statement: dict[str, Any], items: list[tuple[int, dict[str, Any], str]], max_chars: int) -> str:
    blocks = []
    for i, res, text in items:
        blocks.append(
            f"[{i}] Alternative: {res.get('alternative_id', '')}\nURL: {res.get('url', '')}\n"
            f"Description and README Excerpt:\n```\n{text[:max_chars]}\n```"
        )
    return (
        f"Candidate Problem: {problem_statement.get('problem', '')}\n"
        f"Target User: {problem_statement.get('target_user', '')}\n"
        f"Category: {problem_statement.get('category', '')}\n\n"
        + "\n\n".join(blocks)
        + "\n\nFor every alternative index, judge whether it solves the same core user problem, the overlap "
        "degree (full, partial, none), and give verbatim supporting quotes. Output strict JSON."
    )


def _index_map(entries: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    by_index: dict[int, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            by_index[int(entry.get("index"))] = entry
        except (TypeError, ValueError):
            continue
    return by_index


def _row_from_entry(
    candidate_repo_id: str,
    res: dict[str, Any],
    rtext: str,
    entry: dict[str, Any],
    problem_statement: dict[str, Any],
    trace: Any | None,
) -> dict[str, Any] | None:
    """A verified same-problem row, or None when the entry is not same-problem or its quote fails.

    The entry was judged either way. A failed quote is not sent back to the model: the guard
    already rejected it.
    """
    if not bool(entry.get("same_problem")):
        return None
    alt_id = str(res.get("alternative_id") or "").strip()
    quotes = entry.get("quotes") or []
    bad = None
    if not isinstance(quotes, list) or not quotes:
        bad = "no quotes provided for same_problem alternative"
    else:
        for q in quotes:
            q_str = str(q or "").strip()
            if not q_str or not quote_in_text(q_str, rtext):
                bad = f"unverified quote '{q_str}' not found in alternative text"
                break
    if bad:
        if trace is not None:
            trace.event(
                agent_name="orchestrator",
                action="UNVERIFIED_ALTERNATIVE_QUOTE",
                input_summary={"alternative": alt_id, "mode": "batch"},
                output_summary={"error": bad},
                status="error",
            )
        return None
    return {
        "repo_id": candidate_repo_id,
        "alternative_id": alt_id,
        "url": str(res.get("url") or f"https://github.com/{alt_id}"),
        "licence_spdx": str(res.get("licence_spdx") or ""),
        "stars": res.get("stars", 0),
        "pushed_at": str(res.get("pushed_at") or ""),
        "excerpt": rtext[:1000],
        "candidate_excerpt": str(problem_statement.get("problem") or ""),
        "required_terms": "",
        "same_problem": True,
        "overlap": str(entry.get("overlap") or "none").strip().lower(),
        "reason": str(entry.get("reason") or "").strip(),
        "quotes": [str(q).strip() for q in quotes if str(q).strip()],
        "quote_verified": True,
        "source": str(res.get("source") or ""),
        "archived": res.get("archived", False),
    }


def _apply_entries(
    entries: list[dict[str, Any]],
    pending: list[tuple[int, dict[str, Any], str]],
    candidate_repo_id: str,
    problem_statement: dict[str, Any],
    trace: Any | None,
) -> tuple[list[dict[str, Any]], list[tuple[int, dict[str, Any], str]]]:
    by_index = _index_map(entries)
    verified: list[dict[str, Any]] = []
    missing: list[tuple[int, dict[str, Any], str]] = []
    for i, res, rtext in pending:
        if i not in by_index:
            missing.append((i, res, rtext))
            continue
        row = _row_from_entry(candidate_repo_id, res, rtext, by_index[i], problem_statement, trace)
        if row is not None:
            verified.append(row)
    return verified, missing


def _per_item(
    problem_statement: dict[str, Any],
    candidate_repo_id: str,
    res: dict[str, Any],
    client: RelevanceClient | None,
    trace: Any | None,
    budget: FallbackBudget | None,
    index: int | None = None,
    chunk_index: int | None = None,
) -> dict[str, Any] | None:
    """Judge one alternative. A per-item infrastructure failure is charged to the shared budget."""
    left = budget.allow() if budget is not None else None
    started = budget.clock() if budget is not None else 0.0
    alt_row, meta = judge_alternative_relevance(
        problem_statement=problem_statement,
        candidate_repo_id=candidate_repo_id,
        search_result=res,
        client=client,
        trace=trace,
        call_budget_s=left,
    )
    overlap = (alt_row or {}).get("overlap", meta.get("overlap"))
    same = True if alt_row is not None else meta.get("same_problem")
    quote_verified = alt_row is not None
    _trace_llm(
        trace, "ALTERNATIVE_CALL", candidate_repo_id,
        str(meta.get("requested_model") or (client.model if client is not None else "")),
        str(meta.get("served_model") or ""),
        {
            "alternative": res.get("alternative_id"),
            "indexes": [index] if index is not None else None,
            "reason": meta.get("error") or meta.get("reason"),
            "overlap": overlap,
            "same_problem": same,
            "quote_verified": quote_verified,
            "chunk_index": chunk_index,
        },
        status="error" if meta.get("error") or meta.get("fallback") else "ok",
        record_model=getattr(client, "trace", None) is None,
    )
    if budget is not None:
        error = str(meta.get("error") or "")
        if error and _infra_error(error):
            budget.charge_failure(budget.clock() - started, error, kind="per_item")
        else:
            budget.note_completed()
    if alt_row is not None and alt_row.get("same_problem"):
        return alt_row
    return None


def judge_alternatives_batch(
    problem_statement: dict[str, Any],
    candidate_repo_id: str,
    search_results: list[dict[str, Any]],
    client: RelevanceClient | None = None,
    trace: Any | None = None,
    max_chars_per_item: int = 900,
    fallback_budget: FallbackBudget | None = None,
    chunk_index: int | None = None,
) -> list[dict[str, Any]] | None:
    """Judge alternatives in one call (step="alternative_batch").

    A same_problem verdict is kept only when every quote is in that alternative's own text.
    Returns the verified same-problem rows. Returns None only when the chunk produced no
    parseable entry after retries, so the caller can judge each item on its own. A reply that
    parses for some indexes is kept: only the missing indexes are retried.
    """
    if client is None:
        client = RelevanceClient()
    items: list[tuple[int, dict[str, Any], str]] = []
    for i, res in enumerate(search_results):
        text = f"{str(res.get('description') or '').strip()}\n{str(res.get('readme') or '').strip()}".strip()
        if text:
            items.append((i, res, text))
    if not items:
        return []

    prompt_record = load_prompt("relevance_alternative")
    system = prompt_record.text + BATCH_INSTRUCTION
    base_prompt = _batch_prompt(problem_statement, items, max_chars_per_item)
    indexes = [i for i, _, _ in items]

    def _fallback(reason: str) -> None:
        if trace is not None:
            trace.event(
                agent_name="orchestrator",
                action="ALTERNATIVE_BATCH_FALLBACK",
                input_summary={"repo_id": candidate_repo_id, "alternatives": len(items)},
                output_summary={"reason": reason, "requested_model": client.model, "served_model": ""},
                status="error",
            )

    def _call(prompt: str, which: list[int]) -> list[dict[str, Any]] | None:
        """One model call. None means the transport failed. [] means the reply had no complete objects."""
        left = None
        started = 0.0
        if fallback_budget is not None:
            if fallback_budget.time_exhausted():
                raise fallback_budget.stop("time_cap")
            left = fallback_budget.remaining()
            started = fallback_budget.clock()
        try:
            text, req_m, srv_m = client.complete(
                prompt, system, repo_id=candidate_repo_id, step="alternative_batch",
                call_budget_s=left,
            )
        except InfraFallbackStop:
            raise
        except Exception as exc:
            if isinstance(exc, RelevanceLLMError) and "refusing to score with a small local model" in str(exc):
                raise
            if fallback_budget is not None:
                fallback_budget.charge_failure(fallback_budget.clock() - started, str(exc), kind="batch")
            _trace_llm(
                trace, "ALTERNATIVE_BATCH_CALL", candidate_repo_id, client.model, "",
                {"indexes": which, "reason": str(exc)[:160], "chunk_index": chunk_index}, status="error",
                record_model=getattr(client, "trace", None) is None,
            )
            raise
        if fallback_budget is not None:
            fallback_budget.note_completed()
        entries = extract_result_entries(text)
        _trace_llm(
            trace, "ALTERNATIVE_BATCH_CALL", candidate_repo_id, req_m, srv_m,
            {"indexes": which, "parsed_indexes": sorted(_index_map(entries)), "chunk_index": chunk_index},
            status="ok" if entries else "error",
            record_model=getattr(client, "trace", None) is None,
        )
        if not entries:
            client.discard_last()
        return entries

    entries: list[dict[str, Any]] | None = None
    last_exc: Exception | None = None
    for attempt in range(BATCH_CALL_ATTEMPTS):
        if fallback_budget is not None and fallback_budget.time_exhausted():
            raise fallback_budget.stop("time_cap")
        prompt = base_prompt if attempt == 0 else base_prompt + _BATCH_RETRY_NOTE
        try:
            entries = _call(prompt, indexes)
        except InfraFallbackStop:
            raise
        except RelevanceLLMError as exc:
            if "refusing to score with a small local model" in str(exc):
                raise
            last_exc = exc
            entries = None
            if attempt + 1 < BATCH_CALL_ATTEMPTS:
                if trace is not None:
                    trace.event(
                        agent_name="orchestrator",
                        action="ALTERNATIVE_BATCH_RETRY",
                        input_summary={"repo_id": candidate_repo_id, "attempt": attempt + 1, "indexes": indexes},
                        output_summary={"reason": str(exc)[:160], "requested_model": client.model, "served_model": ""},
                        status="error",
                    )
                continue
            _fallback(f"batch call failed after {BATCH_CALL_ATTEMPTS} attempts: {str(exc)[:160]}")
            return None
        except InfraFallbackStop:
            raise
        except Exception as exc:
            last_exc = exc
            entries = None
            if attempt + 1 < BATCH_CALL_ATTEMPTS:
                if trace is not None:
                    trace.event(
                        agent_name="orchestrator",
                        action="ALTERNATIVE_BATCH_RETRY",
                        input_summary={"repo_id": candidate_repo_id, "attempt": attempt + 1, "indexes": indexes},
                        output_summary={"reason": str(exc)[:160], "requested_model": client.model, "served_model": ""},
                        status="error",
                    )
                continue
            _fallback(f"batch call failed after {BATCH_CALL_ATTEMPTS} attempts: {str(exc)[:160]}")
            return None
        if entries:
            break
    if not entries:
        why = "batch answer unusable after retries (invalid JSON or missing indexes)"
        if last_exc is not None:
            why = f"batch call failed after {BATCH_CALL_ATTEMPTS} attempts: {str(last_exc)[:160]}"
        _fallback(why)
        return None

    verified, missing = _apply_entries(entries, items, candidate_repo_id, problem_statement, trace)
    if missing:
        # One follow-up for the indexes the first reply did not contain. A single missing
        # item goes straight to the per-item judge; two or more share one smaller batch.
        if len(missing) >= 2:
            follow_prompt = _batch_prompt(problem_statement, missing, max_chars_per_item)
            try:
                follow = _call(follow_prompt, [i for i, _, _ in missing])
            except InfraFallbackStop as exc:
                exc.attach(verified, 0)
                raise
            except RelevanceLLMError as exc:
                if "refusing to score with a small local model" in str(exc):
                    raise
                follow = None
                if trace is not None:
                    trace.event(
                        agent_name="orchestrator",
                        action="ALTERNATIVE_BATCH_RETRY",
                        input_summary={"repo_id": candidate_repo_id, "indexes": [i for i, _, _ in missing]},
                        output_summary={"reason": str(exc)[:160], "requested_model": client.model, "served_model": ""},
                        status="error",
                    )
            except Exception as exc:
                follow = None
                if trace is not None:
                    trace.event(
                        agent_name="orchestrator",
                        action="ALTERNATIVE_BATCH_RETRY",
                        input_summary={"repo_id": candidate_repo_id, "indexes": [i for i, _, _ in missing]},
                        output_summary={"reason": str(exc)[:160], "requested_model": client.model, "served_model": ""},
                        status="error",
                    )
            if follow:
                more, missing = _apply_entries(follow, missing, candidate_repo_id, problem_statement, trace)
                verified.extend(more)
        try:
            for _i, res, _rtext in missing:
                alt_row = _per_item(
                    problem_statement, candidate_repo_id, res, client, trace, fallback_budget, _i,
                    chunk_index=chunk_index,
                )
                if alt_row is not None:
                    verified.append(alt_row)
        except InfraFallbackStop as exc:
            exc.attach(verified, 0)
            raise
    return verified


def _trace_own(trace: Any | None, repo_id: str, identity: Any, dropped: list[dict[str, str]]) -> None:
    if trace is None or not dropped:
        return
    trace.event(
        agent_name="orchestrator",
        action="own_identity",
        input_summary={"repo_id": repo_id},
        output_summary={
            "own_identity": dropped,
            "identities": identity.as_dict() if hasattr(identity, "as_dict") else {},
        },
        status="ok",
    )


def _trace_partial_alternatives(
    trace: Any | None,
    repo_id: str,
    searched: int,
    verified: list[dict[str, Any]],
    exc: InfraFallbackStop,
) -> None:
    """Record the rows already judged before the alternatives phase stops."""
    if trace is None:
        return
    trace.event(
        agent_name="orchestrator",
        action="ALTERNATIVES_VERIFIED",
        input_summary={"repo_id": repo_id, "searched": searched, "partial": True},
        output_summary={
            "partial": True,
            "unjudged_chunks": exc.unjudged_chunks,
            "cause": exc.cause,
            "completed_calls": exc.completed_calls,
            "batch_502s": exc.batch_502s,
            "per_item_502s": exc.per_item_502s,
            "failures": exc.failures,
            "verified_chunk_indexes": list(exc.verified_chunk_indexes),
            "alternatives": [
                {
                    "alternative_id": row.get("alternative_id"),
                    "url": row.get("url"),
                    "source": row.get("source"),
                    "overlap": row.get("overlap"),
                    "same_problem": row.get("same_problem"),
                    "quote_verified": row.get("quote_verified"),
                    "reason": row.get("reason"),
                    "quotes": row.get("quotes"),
                }
                for row in verified
            ],
        },
        status="ok",
    )


def filter_relevant_alternatives(
    problem_statement: dict[str, Any],
    candidate_repo_id: str,
    search_results: list[dict[str, Any]],
    client: RelevanceClient | None = None,
    trace: Any | None = None,
    candidate: dict[str, Any] | None = None,
    fallback_budget: FallbackBudget | None = None,
    clock: Callable[[], float] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate candidate search results and return only those with same_problem=true and verified quotes.

    Tries one batched LLM call per chunk of ALTERNATIVE_CHUNK_SIZE. A chunk that returns no
    parseable entry falls back to one call per alternative for that chunk only. A chunk that
    parses in part is finished inside judge_alternatives_batch, which retries only the missing items.
    Batch retries share one per-repo time budget: max(600 seconds, 90 seconds times the chunk
    count), then an overall cap. A call that returns a judgement does not consume it. A finished
    batch chunk is reused from the relevance cache, whose key includes the requested model.
    Repeated infrastructure failures, or that budget running out, raise InfraFallbackStop
    carrying the rows already verified, the chunk indexes that finished, and how many chunks
    were left unjudged.
    The candidate's own package, docs, homepage, fork, and mirror are removed before the call
    and again from whatever the model returns.
    """
    from app.agents.own_identity import build_identity, split_own

    rows = list(search_results)
    identity = build_identity(candidate or {"repo_id": candidate_repo_id})
    rows, dropped = split_own(identity, rows)
    _trace_own(trace, candidate_repo_id, identity, dropped)
    chunk_count = (len(rows) + ALTERNATIVE_CHUNK_SIZE - 1) // ALTERNATIVE_CHUNK_SIZE if rows else 0
    if fallback_budget is not None:
        budget = fallback_budget
    else:
        budget = FallbackBudget(seconds=alternatives_budget_seconds(chunk_count), clock=clock)
    verified_alts: list[dict[str, Any]] = []
    verified_chunk_indexes: list[int] = []
    finished = 0
    try:
        for chunk_index, start in enumerate(range(0, len(rows), ALTERNATIVE_CHUNK_SIZE)):
            budget.guard_new_chunk()
            chunk = rows[start:start + ALTERNATIVE_CHUNK_SIZE]
            judged = False
            if len(chunk) > 1:
                batch = judge_alternatives_batch(
                    problem_statement, candidate_repo_id, chunk, client=client, trace=trace,
                    fallback_budget=budget, chunk_index=chunk_index,
                )
                if batch is not None:
                    verified_alts.extend(batch)
                    judged = True
            if not judged:
                if budget.time_exhausted():
                    raise budget.stop("time_cap")
                per_item_failures = 0
                for res in chunk:
                    if budget.time_exhausted():
                        raise budget.stop("time_cap")
                    before = budget.failures
                    alt_row = _per_item(
                        problem_statement, candidate_repo_id, res, client, trace, budget,
                        chunk_index=chunk_index,
                    )
                    if budget.failures > before:
                        per_item_failures += 1
                    if alt_row is not None:
                        verified_alts.append(alt_row)
                # A batch that failed open can still finish on the per-item salvage.
                # Those rows are judged. A per-item infrastructure error leaves the chunk unjudged.
                judged = per_item_failures == 0
            if judged:
                verified_chunk_indexes.append(chunk_index)
                finished += 1
            elif budget.failures >= FALLBACK_INFRA_LIMIT:
                raise budget.stop("infra_failures")
            elif budget.time_exhausted():
                raise budget.stop("time_cap")
    except InfraFallbackStop as exc:
        verified_alts, dropped_after = split_own(identity, verified_alts)
        _trace_own(trace, candidate_repo_id, identity, dropped_after)
        exc.attach(verified_alts, max(0, chunk_count - finished), verified_chunk_indexes)
        _trace_partial_alternatives(trace, candidate_repo_id, len(search_results), exc.verified, exc)
        raise
    verified_alts, dropped_after = split_own(identity, verified_alts)
    _trace_own(trace, candidate_repo_id, identity, dropped_after)
    return verified_alts


def judge_partial_alternatives(
    candidate: dict[str, Any],
    verified: list[dict[str, Any]],
    *,
    unjudged_chunks: int,
    infra_reason: str,
    as_of: Any | None = None,
) -> DiscoveryDecision:
    """Apply the maturity rule to alternatives judged before the phase stopped.

    A verified full overlap blocks only when ``evaluate_blocker`` says it blocks.
    An unverified quote, a partial overlap, or an immature product does not block.
    Unjudged chunks never produce CANDIDATE.
    """
    from datetime import date as _date

    from app.agents.blocker import evaluate_blocker

    when = as_of or _date.today()
    blocking: list[dict[str, Any]] = []
    must_beat: list[dict[str, str]] = []
    labels: list[str] = []
    for row in verified:
        name = str(row.get("alternative_id") or row.get("url") or "").strip()
        overlap = str(row.get("overlap") or "").strip().lower()
        quotes = [str(q).strip() for q in (row.get("quotes") or []) if str(q).strip()]
        quote_ok = row.get("quote_verified") is not False and bool(quotes)
        labels.append(
            f"{name} (overlap {overlap or 'unknown'}, {'quote verified' if quote_ok else 'quote unverified'})"
        )
        if row.get("same_problem") is not True or overlap != "full" or not quote_ok:
            continue
        decision = evaluate_blocker(candidate, row, as_of=when)
        if decision.get("blocks") and decision.get("verdict_if_blocked") == "ALREADY_EXISTS":
            blocking.append(row)
            continue
        must_beat.append({
            "alternative": name,
            "overlap": overlap,
            "quote": quotes[0] if quotes else "",
            "reason": "did not block: " + "; ".join(decision.get("reasons") or []),
        })
    if blocking:
        chosen = blocking[0]
        quotes = [str(q).strip() for q in (chosen.get("quotes") or []) if str(q).strip()]
        name = chosen.get("url") or chosen.get("alternative_id")
        reason = (
            f"verified full-overlap alternative {name} already solves the same problem"
            + (f" (quote: '{quotes[0]}')" if quotes else "")
        )
        if unjudged_chunks:
            reason += f". Alternatives phase stopped with {unjudged_chunks} chunk(s) unjudged"
        picked = [{**chosen, "quotes": quotes, "overlap": "full", "reason": reason}]
        return DiscoveryDecision("ALREADY_EXISTS", reason, _evidence_rows(picked), must_beat)
    shown = ", ".join(labels) if labels else "none"
    reason = f"{infra_reason}; {unjudged_chunks} chunk(s) unjudged; verified: {shown}"
    return DiscoveryDecision("NEEDS_REVIEW", reason, [], must_beat)
