from adapters.meterless import MeterlessAdapter
from adapters.zetaris import ZetarisAdapter
from config.settings import Settings


def build_adapters(settings: Settings, mode: str) -> tuple[ZetarisAdapter, MeterlessAdapter]:
    return ZetarisAdapter(settings, mode), MeterlessAdapter(settings, mode)
