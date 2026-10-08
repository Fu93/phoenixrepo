from typing import Any

from config.settings import Settings


class ZetarisAdapter:
    """Interface only. No network call before the build window."""

    def __init__(self, settings: Settings, mode: str) -> None:
        self.settings = settings
        self._mode = mode

    def configured(self) -> bool:
        return bool(self.settings.zetaris_api_key and self.settings.zetaris_mcp_url)

    def enabled(self) -> bool:
        return False

    def provider(self) -> str:
        return "zetaris"

    def mode(self) -> str:
        return self._mode

    def status(self) -> dict[str, Any]:
        return {
            "provider": self.provider(),
            "configured": self.configured(),
            "enabled": self.enabled(),
            "integrated": False,
            "integration": "INTERFACE_ONLY",
            "mode": self.mode(),
        }

    def collect(self, target: str) -> list[dict[str, Any]]:
        raise NotImplementedError("Zetaris query is blocked before 2026-10-22 00:00 UTC.")
