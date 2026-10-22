from dataclasses import dataclass

from app.adapters.meterless import MeterlessRuntime
from app.adapters.nvidia import NvidiaDirect
from app.errors import InvestigationError
from app.llm.standin import SampleStandIn
from config.settings import Settings


@dataclass
class BoundLLM:
    inner: object
    provider: str
    model: str
    sample_standin: bool
    requested_model: str = ""
    served_model: str | None = None

    def __post_init__(self) -> None:
        self.requested_model = self.model
        self.served_model = self.model if self.sample_standin else None

    def complete(self, messages: list[dict[str, str]]) -> str:
        self.requested_model = self.model
        text = self.inner.complete(messages, model=self.model)  # type: ignore[attr-defined]
        self.requested_model = getattr(self.inner, "last_requested_model", self.model)
        served = getattr(self.inner, "last_served_model", None)
        if served:
            self.served_model = served
        elif self.sample_standin:
            self.served_model = self.model
        return text

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
        timeout_seconds=settings.meterless_timeout_seconds,
        max_tokens=settings.meterless_max_tokens,
    )
    if not client.configured():
        raise InvestigationError(
            "MeterlessNotConfigured",
            "Live mode requires METERLESS_RUNTIME_TOKEN. Sample mode is not used as a fallback.",
            status_code=400,
        )
    return BoundLLM(client, "meterless", client.model, False)
