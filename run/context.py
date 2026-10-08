from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from run.fingerprint import run_fingerprint

PIPELINE_VERSION = "0.1.0"


class RunContext(BaseModel):
    run_id: str
    started_at: datetime
    repository: str
    mode: str
    max_repair_iterations: int
    foundation_only: bool = True
    pipeline_version: str = PIPELINE_VERSION
    fingerprint: str
    metadata: dict = Field(default_factory=dict)

    @classmethod
    def create(cls, repository: str, mode: str, max_repair_iterations: int) -> "RunContext":
        return cls(
            run_id=f"phoenix-{uuid4().hex[:8]}",
            started_at=datetime.now(timezone.utc),
            repository=repository,
            mode=mode,
            max_repair_iterations=max_repair_iterations,
            pipeline_version=PIPELINE_VERSION,
            fingerprint=run_fingerprint(repository, mode, max_repair_iterations, PIPELINE_VERSION),
        )
