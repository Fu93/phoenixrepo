"""Foundation entry point. Does not resurrect a repository."""

import os
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI

from config.settings import Settings
from tracing.trace import write_trace
from schemas.input import RunRequest
from schemas.output import RunResponse

app = FastAPI(title="PhoenixRepo", version="0.1.0-foundation")
settings = Settings()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "foundation": True, "sample_mode": settings.sample_mode}


@app.post("/run", response_model=RunResponse)
def run(request: RunRequest) -> RunResponse:
    run_id = f"phoenix-{uuid4().hex[:8]}"
    mode = "sample" if settings.sample_mode or request.mode == "sample" else "live"
    log_path = write_trace(
        Path(settings.log_dir),
        run_id,
        {
            "agent_name": "Orchestrator",
            "action": "ingest_only",
            "input_summary": request.repository,
            "output_summary": "Foundation ingest. No resurrection path.",
            "status": "success",
            "sample_mode": mode == "sample",
            "retry_count": 0,
        },
    )
    return RunResponse(
        run_id=run_id,
        status="RUNNING",
        stage="INGESTED",
        confidence=None,
        decision=None,
        metadata={"mode": mode, "repository": request.repository, "log_file": str(log_path)},
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", str(settings.port))))
