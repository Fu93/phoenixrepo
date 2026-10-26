"""HTTP entry point. Sample mode runs the investigation on the Zetaris cache."""

import os

import uvicorn
from fastapi import FastAPI, Request
from pydantic import BaseModel
from fastapi.responses import FileResponse, JSONResponse

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


class JudgeRequest(BaseModel):
    url: str
    use_llm: bool = True


@app.post("/judge")
def judge_endpoint(request: JudgeRequest):
    """Understand a repository, search the whole web, verify quotes, return a verdict and report path."""
    from scripts import judge_repo
    from scripts.score_candidates import RelevanceLLMError

    try:
        return judge_repo.judge(request.url, use_llm=request.use_llm)
    except judge_repo.JudgeError as exc:
        return JSONResponse(status_code=422, content={"error": {"type": "CannotJudge", "message": str(exc)}})
    except RelevanceLLMError as exc:
        return JSONResponse(status_code=502, content={"error": {"type": "LLMUnavailable", "message": str(exc)}})


@app.get("/ui")
def replay_ui():
    """Static replay page. No build step and no external assets."""

    from app.replay_ui import ui_path

    return FileResponse(ui_path(), media_type="text/html; charset=utf-8")


@app.get("/api/replays")
def list_replays():
    from app.replay_ui import catalog

    return catalog()


@app.get("/api/replays/{replay_id}")
def get_replay(replay_id: str):
    from app.replay_ui import ReplayError, replay_payload

    try:
        return replay_payload(replay_id)
    except ReplayError as exc:
        return JSONResponse(status_code=exc.status, content={"error": {"type": "Replay", "message": str(exc)}})


@app.post("/api/replays/{replay_id}/tamper")
def tamper_replay_endpoint(replay_id: str):
    """Verify a one-character copy of the committed trace. The file is left as committed."""

    from app.replay_ui import ReplayError, tamper_replay

    try:
        return tamper_replay(replay_id)
    except ReplayError as exc:
        return JSONResponse(status_code=exc.status, content={"error": {"type": "Replay", "message": str(exc)}})


@app.get("/api/live")
def live_status():
    from app.replay_ui import live_status as status

    return status(get_settings())


@app.exception_handler(InvestigationError)
def investigation_error(_: Request, exc: InvestigationError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.body())


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
