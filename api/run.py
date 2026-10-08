"""Foundation entry point. Sample pipeline only. No resurrection."""

import os

import uvicorn
from fastapi import FastAPI, HTTPException

from config.settings import Settings
from foundation.orchestrator import FoundationOrchestrator
from schemas.input import RunRequest
from schemas.output import RunResponse

app = FastAPI(title="PhoenixRepo", version="0.1.0-foundation")
settings = Settings()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "foundation": True, "sample_mode": settings.sample_mode}


@app.post("/run", response_model=RunResponse)
def run(request: RunRequest) -> RunResponse:
    if request.mode != "sample":
        raise HTTPException(
            status_code=400,
            detail="Live repository execution is not available before the hackathon opening.",
        )
    result = FoundationOrchestrator(
        repository=request.repository,
        mode=request.mode,
        max_repair_iterations=request.max_repair_iterations,
    ).run()
    return RunResponse(**result)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
