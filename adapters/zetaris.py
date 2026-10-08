from abc import ABC, abstractmethod
from typing import Any


class EvidenceSource(ABC):
    @abstractmethod
    def collect(self, target: str) -> list[dict[str, Any]]:
        raise NotImplementedError


class ZetarisAdapter(EvidenceSource):
    def collect(self, target: str) -> list[dict[str, Any]]:
        raise NotImplementedError("Zetaris integration is not active yet.")
