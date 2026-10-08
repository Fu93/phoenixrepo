from typing import Any

from config.settings import Settings


class MeterlessAdapter:
    """Interface only. No network call before the build window."""

    def __init__(self, settings: Settings, mode: str) -> None:
        self.settings = settings
        self._mode = mode

    def configured(self) -> bool:
        return bool(self.settings.meterless_api_key)

    def enabled(self) -> bool:
        return False

    def provider(self) -> str:
        return "meterless"

    def mode(self) -> str:
        return self._mode

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider(),
            "configured": self.configured(),
            "enabled": self.enabled(),
            "integrated": False,
            "mode": self.mode(),
        }

    def record(self, event: str, data: dict[str, Any]) -> None:
        raise NotImplementedError("Meterless write is blocked before 2026-10-22 00:00 UTC.")
