"""Foundation entry point. Does not resurrect a repository."""

from uuid import uuid4

from fastapi import FastAPI

from config.settings import Settings
from schemas.input import RunRequest
from schemas.output import RunResponse
from trace.logger import TraceLogger

app = FastAPI(title="PhoenixRepo", version="0.1.0-foundation")
settings = Settings()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "foundation": True, "sample_mode": settings.sample_mode}


@app.post("/run", response_model=RunResponse)
def run(request: RunRequest) -> RunResponse:
    run_id = f"phoenix-{uuid4().hex[:8]}"
    mode = "sample" if settings.sample_mode or request.mode == "sample" else request.mode
    trace = TraceLogger(run_id)
    trace.event("RUN_STARTED", repository=request.repository, mode=mode)
    trace.event("STATE_ENTERED", state="INGESTED")
    return RunResponse(
        run_id=run_id,
        status="RUNNING",
        stage="INGESTED",
        confidence=None,
        decision=None,
        metadata={
            "mode": mode,
            "repository": request.repository,
            "log_file": str(trace.path),
        },
    )
