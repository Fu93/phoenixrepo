from abc import ABC, abstractmethod
from typing import Any


class ExecutionObserver(ABC):
    @abstractmethod
    def record(self, event: str, data: dict[str, Any]) -> None:
        raise NotImplementedError


class MeterlessAdapter(ExecutionObserver):
    def record(self, event: str, data: dict[str, Any]) -> None:
        raise NotImplementedError("Meterless integration is not active yet.")
