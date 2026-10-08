"""Interface only. No scored memory path before 22 October 2026 00:00 UTC."""


class MeterlessAdapter:
    def __init__(self, api_key: str | None) -> None:
        self.api_key = api_key

    def configured(self) -> bool:
        return bool(self.api_key)

    def write(self, key: str, value: dict) -> None:
        raise NotImplementedError("Meterless write is wired after the build window opens.")

    def read(self, key: str) -> dict | None:
        raise NotImplementedError("Meterless read is wired after the build window opens.")
