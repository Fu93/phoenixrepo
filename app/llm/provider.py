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
# A lane that failed is skipped only for this long. A permanent skip turned one 502 burst into
# deterministic stand-ins for every later role in the run (hygen run phoenix-894721ec).
LANE_COOLDOWN_SECONDS = 45.0
LANE_COOLDOWN_WAIT_CAP = 30.0


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
    failed_lanes: dict[str, float] = field(default_factory=dict)
    clock: Callable[[], float] = time.monotonic
    _budget_lanes: set[str] = field(default_factory=set)
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
        for round_no in range(2):
            attempted = False
            for lane in self.lanes:
                if self._is_failed(lane):
                    self._remember(lane, "skipped-failed-lane")
                    last = MeterlessError(
                        f"Nightshift lane {lane} failed recently (cooling down).",
                        status_code=502,
                    )
                    continue
                attempted = True
                kind, outcome = self._lane(messages, lane, deadline)
                if kind == "ok":
                    return str(outcome)
                last = outcome if isinstance(outcome, BaseException) else MeterlessError(str(outcome), status_code=502)
                if kind == "stop":
                    raise last
            if attempted or round_no == 1:
                break
            if all(lane in self._budget_lanes for lane in self.failed_lanes):
                break  # lanes are merely too slow for the step budget; waiting will not help
            # Every lane is cooling down from an earlier burst of errors: wait for the earliest
            # one to recover (bounded) and try again instead of degrading to a stand-in.
            now = self.clock()
            wait = min((exp - now for exp in self.failed_lanes.values() if exp > now), default=0.0)
            if deadline is not None:
                wait = min(wait, max(0.0, deadline - time.monotonic() - 1.0))
            wait = max(0.0, min(wait, LANE_COOLDOWN_WAIT_CAP))
            if wait > 0:
                self.sleeper(wait)
            self.failed_lanes.clear()
        if last is None:
            raise MeterlessError("Meterless lanes were empty.", status_code=502)
        raise last

    def _fail(self, lane: str, budget: bool = False) -> None:
        self.failed_lanes[lane] = self.clock() + LANE_COOLDOWN_SECONDS
        if budget:
            self._budget_lanes.add(lane)
        else:
            self._budget_lanes.discard(lane)

    def _is_failed(self, lane: str) -> bool:
        expiry = self.failed_lanes.get(lane)
        if expiry is None:
            return False
        if expiry <= self.clock():
            del self.failed_lanes[lane]
            return False
        return True

    def _lane(self, messages: list[dict[str, str]], lane: str, deadline: float | None) -> tuple[str, object]:
        delays = (0, *_RETRY_DELAYS)
        last: BaseException | None = None
        for attempt, delay in enumerate(delays):
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or (delay and delay >= remaining):
                    self._fail(lane, budget=True)
                    self._remember(lane, "budget")
                    return "next", MeterlessError("Nightshift step budget exhausted.", status_code=504)
            if delay:
                self.sleeper(delay)
            timeout = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._fail(lane, budget=True)
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
                    self._fail(lane, budget=deadline is not None and time.monotonic() >= deadline)
                    return "next", exc
        self._fail(lane, budget=deadline is not None and time.monotonic() >= deadline)
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

    def _call(self, messages: list[dict[str, str]], model: str, timeout: float | None) -> tuple[str, str, str | None]:
        """One provider call. Returns (text, requested_model, served_model) and touches no state on self."""
        text = self._invoke(messages, model, timeout)
        requested = getattr(self.inner, "last_requested_model", model) or model
        served = getattr(self.inner, "last_served_model", None) or None
        if served is None and self.sample_standin:
            served = model
        return text, requested, served

    def _record(self, model: str, requested: str, served: str | None) -> None:
        self.lane = model
        self.requested_model = requested
        # Always overwrite: keeping the previous value when a reply has no `model` field put the
        # last call's served model on this call's trace line.
        self.served_model = served

    def _once(self, messages: list[dict[str, str]], model: str, timeout: float | None = None) -> str:
        self.lane = model
        self.requested_model = model
        text, requested, served = self._call(messages, model, timeout)
        self._record(model, requested, served)
        return text

    def _once_bounded(self, messages: list[dict[str, str]], model: str, timeout: float) -> str:
        """Wait at most `timeout` seconds. A late attempt is abandoned, not joined again.

        The worker thread only fills its own box. Provenance is written by the caller after a
        successful join, so an abandoned attempt that finishes later cannot overwrite
        requested_model / served_model for the call that replaced it.
        """

        box: dict = {}
        self.lane = model
        self.requested_model = model

        def run() -> None:
            try:
                box["result"] = self._call(messages, model, timeout)
            except Exception as exc:
                box["error"] = exc

        worker = threading.Thread(target=run, name="nightshift-attempt", daemon=True)
        worker.start()
        worker.join(timeout)
        if worker.is_alive():
            raise MeterlessError(f"Meterless chat timed out after {timeout}s.")
        if "error" in box:
            raise box["error"]
        text, requested, served = box.get("result") or ("", model, None)
        self._record(model, requested, served)
        return str(text or "")

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
    lanes = settings.meterless_lanes_list()
    req = settings.requested_model()
    if req and (not lanes or lanes[0] != req):
        lanes = (req, *(l for l in lanes if l != req))
    return BoundLLM(
        client,
        "meterless",
        client.model,
        False,
        lanes=lanes,
    )
