"""HTTP entry point. Sample mode runs the investigation on the Zetaris cache."""

import os

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.errors import InvestigationError
from app.investigation import InvestigationOrchestrator
from config.settings import Settings
from config.versions import INVESTIGATION_VERSION, PIPELINE_VERSION
from schemas.input import RunRequest
from schemas.output import RunResponse

app = FastAPI(title="PhoenixRepo", version=INVESTIGATION_VERSION)


def get_settings() -> Settings:
    return Settings()


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    provider = "sample-standin" if settings.sample_mode else settings.llm_provider
    return {
        "status": "ok",
        "service": "phoenixrepo",
        "pipeline_version": PIPELINE_VERSION,
        "investigation_version": INVESTIGATION_VERSION,
        "sample_mode": settings.sample_mode,
        "llm_provider": provider,
        "meterless_primary": settings.llm_provider == "meterless",
    }


@app.post("/run", response_model=RunResponse)
def run(request: RunRequest):
    settings = get_settings()
    if request.mode == "live" and settings.sample_mode:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "type": "SampleMode",
                    "message": "mode live is refused while SAMPLE_MODE=true. Set SAMPLE_MODE=false and provide Zetaris and Meterless credentials. Sample mode is not a silent fallback.",
                }
            },
        )
    if request.mode == "live" and not settings.zetaris_api_key:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "type": "ZetarisNotConfigured",
                    "message": "Live mode requires ZETARIS_API_KEY. The investigation has no other evidence source.",
                }
            },
        )
    try:
        result = InvestigationOrchestrator(
            repository=request.repository,
            mode=request.mode,
            max_repair_iterations=request.max_repair_iterations,
            settings=settings,
        ).run()
    except InvestigationError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.body())
    except ValueError as exc:
        return JSONResponse(
            status_code=422,
            content={"error": {"type": "InvalidRepository", "message": str(exc)}},
        )
    return RunResponse(**result)


@app.exception_handler(InvestigationError)
def investigation_error(_: Request, exc: InvestigationError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.body())


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
