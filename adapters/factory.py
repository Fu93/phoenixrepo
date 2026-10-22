from app.adapters.meterless import MeterlessRuntime
from app.adapters.sample_zetaris import SampleZetaris
from app.adapters.zetaris import ZetarisClient
from app.errors import ZetarisError
from config.settings import Settings


def build_adapters(settings: Settings, mode: str):
    """Returns the Zetaris evidence adapter and the Meterless Runtime provider."""

    if mode == "live" and not settings.sample_mode:
        zetaris = (
            ZetarisClient(
                rest_url=settings.zetaris_rest_url,
                org_id=settings.zetaris_org_id,
                api_key=settings.zetaris_api_key,
            )
            if settings.zetaris_api_key
            else UnconfiguredZetaris()
        )
    else:
        zetaris = SampleZetaris()
    meterless = MeterlessRuntime(
        base_url=settings.meterless_base_url,
        token=settings.meterless_runtime_token,
        model=settings.meterless_model,
        app_id=settings.meterless_app,
        timeout_seconds=settings.meterless_timeout_seconds,
    )
    return zetaris, meterless


class UnconfiguredZetaris:
    """Live adapter with no key. Queries fail instead of reading another source."""

    def provider(self) -> str:
        return "zetaris"

    def configured(self) -> bool:
        return False

    def enabled(self) -> bool:
        return False

    def mode(self) -> str:
        return "live"

    def status(self) -> dict:
        return {
            "provider": "zetaris",
            "configured": False,
            "enabled": False,
            "integrated": True,
            "integration": "REST",
            "load_bearing": True,
            "mode": "live",
        }

    def run_sql(self, sql: str, limit: int = 100):
        raise ZetarisError("ZETARIS_API_KEY is missing.", status_code=400)
