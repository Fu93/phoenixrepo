from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from config.versions import SCHEMA_VERSION


class EvidencePack(BaseModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    created_at: datetime
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    integrity_hash: str | None = None
