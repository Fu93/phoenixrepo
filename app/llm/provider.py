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

    def complete(self, messages: list[dict[str, str]]) -> str:
        return self.inner.complete(messages, model=self.model)  # type: ignore[attr-defined]

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
        model=settings.meterless_model,
        app_id=settings.meterless_app,
        timeout_seconds=settings.meterless_timeout_seconds,
    )
    if not client.configured():
        raise InvestigationError(
            "MeterlessNotConfigured",
            "Live mode requires METERLESS_RUNTIME_TOKEN. Sample mode is not used as a fallback.",
            status_code=400,
        )
    return BoundLLM(client, "meterless", client.model, False)
