from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    repository: str = Field(..., description="Repository URL or repository identifier.")
    mode: str = Field(default="sample", pattern="^(sample|live)$")
    max_repair_iterations: int = Field(default=2, ge=0, le=10)
