from dataclasses import dataclass, field
import inspect
import threading
import time
from typing import Callable

from app.adapters.meterless import MeterlessError, MeterlessRuntime
from app.adapters.nvidia import NvidiaDirect
from app.errors import InvestigationError
from app.llm.standin import SampleStandIn
from config.settings import Settings

_RETRY_DELAYS = (2, 4, 8)


def retry_reason(exc: BaseException) -> str | None:
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status == 429 or "http 429" in text:
        return "429"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if status == 502 or "http 502" in text:
        return "502"
    return None


def is_unauthorized(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if status in {401, 403}:
        return True
    text = str(exc).lower()
    return "unauthorized" in text or "rejected the runtime token" in text


@dataclass
class BoundLLM:
    inner: object
    provider: str
    model: str
    sample_standin: bool
    requested_model: str = ""
    served_model: str | None = None
    lanes: tuple[str, ...] = ()
    sleeper: Callable[[float], None] = time.sleep
    fallback_log: list[dict] = field(default_factory=list)
    failed_lanes: set[str] = field(default_factory=set)
    lane: str = ""

    def __post_init__(self) -> None:
        self.requested_model = self.model
        self.served_model = self.model if self.sample_standin else None
        if not self.lanes:
            self.lanes = (self.model,)

    def complete(self, messages: list[dict[str, str]], deadline: float | None = None) -> str:
        if self.sample_standin or self.provider != "meterless":
            return self._once(messages, self.model)
        last: BaseException | None = None
        for lane in self.lanes:
            if lane in self.failed_lanes:
                self._remember(lane, "skipped-failed-lane")
                last = MeterlessError(
                    f"Nightshift lane {lane} already failed in this run.",
                    status_code=502,
                )
                continue
            kind, outcome = self._lane(messages, lane, deadline)
            if kind == "ok":
                return str(outcome)
            last = outcome if isinstance(outcome, BaseException) else MeterlessError(str(outcome), status_code=502)
            if kind == "stop":
                raise last
        if last is None:
            raise MeterlessError("Meterless lanes were empty.", status_code=502)
        raise last

    def _lane(self, messages: list[dict[str, str]], lane: str, deadline: float | None) -> tuple[str, object]:
        delays = (0, *_RETRY_DELAYS)
        last: BaseException | None = None
        for attempt, delay in enumerate(delays):
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or (delay and delay >= remaining):
                    self.failed_lanes.add(lane)
                    self._remember(lane, "budget")
                    return "next", MeterlessError("Nightshift step budget exhausted.", status_code=504)
            if delay:
                self.sleeper(delay)
            timeout = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.failed_lanes.add(lane)
                    self._remember(lane, "budget")
                    return "next", MeterlessError("Nightshift step budget exhausted.", status_code=504)
                timeout = min(self._client_timeout(), remaining)
            try:
                if deadline is None:
                    return "ok", self._once(messages, lane)
                return "ok", self._once_bounded(messages, lane, timeout or 0.0)
            except Exception as exc:
                last = exc
                reason = "unauthorized" if is_unauthorized(exc) else (retry_reason(exc) or "error")
                self._remember(lane, reason)
                if is_unauthorized(exc):
                    return "stop", exc
                if retry_reason(exc) is None or attempt == len(delays) - 1:
                    self.failed_lanes.add(lane)
                    return "next", exc
        self.failed_lanes.add(lane)
        return "next", last or MeterlessError("Nightshift lane failed.", status_code=502)

    def _client_timeout(self) -> float:
        value = getattr(self.inner, "timeout_seconds", None)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return 180.0

    def _accepts(self, name: str) -> bool:
        try:
            signature = inspect.signature(self.inner.complete)  # type: ignore[attr-defined]
        except (TypeError, ValueError):
            return False
        if name in signature.parameters:
            return True
        return any(item.kind == inspect.Parameter.VAR_KEYWORD for item in signature.parameters.values())

    def _invoke(self, messages: list[dict[str, str]], model: str, timeout: float | None) -> str:
        kwargs: dict = {"model": model}
        if timeout is not None and self._accepts("timeout"):
            kwargs["timeout"] = timeout
        return self.inner.complete(messages, **kwargs)  # type: ignore[attr-defined]

    def _once(self, messages: list[dict[str, str]], model: str, timeout: float | None = None) -> str:
        self.lane = model
        self.requested_model = model
        text = self._invoke(messages, model, timeout)
        self.requested_model = getattr(self.inner, "last_requested_model", model) or model
        served = getattr(self.inner, "last_served_model", None)
        if served:
            self.served_model = served
        elif self.sample_standin:
            self.served_model = model
        return text

    def _once_bounded(self, messages: list[dict[str, str]], model: str, timeout: float) -> str:
        """Wait at most `timeout` seconds. A late attempt is abandoned, not joined again."""

        box: dict = {}

        def run() -> None:
            try:
                box["text"] = self._once(messages, model, timeout=timeout)
            except Exception as exc:
                box["error"] = exc

        worker = threading.Thread(target=run, name="nightshift-attempt", daemon=True)
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            raise MeterlessError(f"Meterless chat timed out after {timeout}s.")
        if "error" in box:
            raise box["error"]
        return str(box.get("text") or "")

    def _remember(self, lane: str, reason: str) -> None:
        self.fallback_log.append(
            {
                "lane": lane,
                "requested_model": self.requested_model or lane,
                "served_model": self.served_model,
                "reason": reason,
            }
        )

    def label(self) -> str:
        if self.sample_standin:
            return (
                "SAMPLE_MODE deterministic stand-in. Not a live model reply. "
                "The stand-in reads the Zetaris rows in the prompt and applies the evidence policy."
            )
        if self.provider == "nvidia":
            return "Secondary NVIDIA-direct dev fallback. Not the Meterless Nightshift route."
        return "Meterless Runtime, the primary model provider."


def build_llm(settings: Settings, *, sample: bool) -> BoundLLM:
    if sample:
        standin = SampleStandIn()
        return BoundLLM(standin, standin.provider_name, standin.model_name, True)
    provider = (settings.llm_provider or "meterless").strip().lower()
    if provider == "nvidia":
        client = NvidiaDirect(
            api_key=settings.nvidia_api_key or "",
            base_url=settings.nvidia_base_url,
            model=settings.nvidia_model,
            timeout_seconds=settings.nvidia_timeout_seconds,
        )
        return BoundLLM(client, "nvidia", client.model, False)
    if provider != "meterless":
        raise InvestigationError(
            "UnknownProvider",
            "llm_provider must be meterless (primary) or nvidia (secondary dev fallback).",
            status_code=400,
        )
    client = MeterlessRuntime(
        base_url=settings.meterless_base_url,
        token=settings.meterless_runtime_token,
        model=settings.requested_model(),
        app_id=settings.meterless_app,
        timeout_seconds=settings.chat_timeout_seconds(),
        max_tokens=settings.meterless_max_tokens,
    )
    if not client.configured():
        raise InvestigationError(
            "MeterlessNotConfigured",
            "Live mode requires METERLESS_RUNTIME_TOKEN. Sample mode is not used as a fallback.",
            status_code=400,
        )
    return BoundLLM(
        client,
        "meterless",
        client.model,
        False,
        lanes=settings.meterless_lanes_list(),
    )
