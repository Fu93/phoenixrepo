"""Markovian chunk loop.

Minimum from the hackathon digest: config, chunk manager, carryover cascade,
orchestrator loop. Each agent step sees the bounded carryover, not the raw
history of earlier steps.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from app.errors import InvestigationError


@dataclass
class MarkovianConfig:
    max_chunks: int = 8
    max_carryover_chars: int = 1200
    chunk_timeout_seconds: float = 120


@dataclass
class ChunkRecord:
    name: str
    carryover_in: str
    carryover_out: str


@dataclass
class ChunkManager:
    config: MarkovianConfig
    clock: Callable[[], float] = time.monotonic
    carryover: str = ""
    chunks: list[ChunkRecord] = field(default_factory=list)

    def run(self, name: str, fn: Callable[[str], tuple[object, str]]):
        if len(self.chunks) >= self.config.max_chunks:
            raise InvestigationError(
                "IterationLimit",
                f"Markovian loop stopped after {self.config.max_chunks} chunks.",
                status_code=500,
            )
        started = self.clock()
        incoming = self.carryover
        result, summary = fn(incoming)
        elapsed = self.clock() - started
        if elapsed > self.config.chunk_timeout_seconds:
            raise InvestigationError(
                "StepTimeout",
                f"Chunk {name} exceeded {self.config.chunk_timeout_seconds}s.",
                status_code=504,
            )
        self.carryover = (summary or "")[: self.config.max_carryover_chars]
        self.chunks.append(ChunkRecord(name=name, carryover_in=incoming, carryover_out=self.carryover))
        return result
