"""HTTP entry point. Sample mode runs the investigation on the Zetaris cache."""

import os

import uvicorn
from fastapi import FastAPI, Request
from pydantic import BaseModel
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
def replay_page():
    """Self-contained replay page. No CDN and no build step."""
    from fastapi.responses import HTMLResponse

    from app.replay import ui_html

    return HTMLResponse(ui_html())


@app.get("/ui/live")
def replay_live():
    from app.replay import live_judge_status

    return live_judge_status()


@app.get("/ui/runs")
def replay_runs():
    from app.replay import list_runs

    return {"runs": list_runs()}


@app.get("/ui/runs/{run_id}")
def replay_run(run_id: str):
    from app.replay import run_detail

    detail = run_detail(run_id)
    if detail is None:
        return JSONResponse(status_code=404, content={"error": {"type": "UnknownRun", "message": "No committed recording uses that id."}})
    return detail


@app.post("/ui/runs/{run_id}/verify")
async def replay_verify(run_id: str, request: Request):
    import json

    from app.replay import verify_run

    raw = await request.body()
    text = None
    if raw.strip():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return JSONResponse(status_code=422, content={"error": {"type": "InvalidJSON", "message": "verify body must be a JSON object"}})
        if not isinstance(payload, dict) or "trace" not in payload or not isinstance(payload.get("trace"), str):
            return JSONResponse(status_code=422, content={"error": {"type": "InvalidJSON", "message": "verify body must include a trace string"}})
        text = payload["trace"]
        if len(text) > 2_000_000:
            return JSONResponse(status_code=413, content={"error": {"type": "TraceTooLarge", "message": "trace text is over 2 MB"}})
    result = verify_run(run_id, text)
    if result is None:
        return JSONResponse(status_code=404, content={"error": {"type": "UnknownRun", "message": "No committed recording uses that id."}})
    return result


@app.post("/ui/runs/{run_id}/tamper")
def replay_tamper(run_id: str):
    from app.replay import tamper_run

    result = tamper_run(run_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": {"type": "UnknownRun", "message": "No committed recording uses that id."}})
    return result


@app.exception_handler(InvestigationError)
def investigation_error(_: Request, exc: InvestigationError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.body())


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
