"""VibeStack HTTP API: start runs, stream progress (SSE), browse files, download output."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.config import settings

from .preview import PreviewError, PreviewManager
from .runs import TERMINAL, RunManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

previews = PreviewManager()


@asynccontextmanager
async def lifespan(_: FastAPI):
    app.state.proxy = httpx.AsyncClient(timeout=30, trust_env=False)
    yield
    await app.state.proxy.aclose()
    previews.stop_all()


app = FastAPI(
    title="VibeStack",
    version="0.1.0",
    description="Agentic natural-language-to-backend harness",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o],
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = RunManager()


class RunRequest(BaseModel):
    prompt: str = Field(min_length=10, max_length=2000)
    fault: Literal["none", "import", "status"] = "import"
    codegen_mode: str | None = Field(default=None, pattern="^(llm|template)$")


def _check_access(code: str | None) -> None:
    if settings.access_code and code != settings.access_code:
        raise HTTPException(status_code=401, detail="invalid access code")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict[str, Any]:
    return {
        "llm_enabled": settings.llm_enabled,
        "fake_llm": settings.fake_llm,
        "codegen_mode": settings.codegen_mode,
        "sandbox": settings.sandbox,
        "access_required": bool(settings.access_code),
        "max_heal_iterations": settings.max_heal_iterations,
        "models": {"strong": settings.strong_model, "cheap": settings.cheap_model, "fallback": settings.fallback_model},
    }


@app.post("/api/runs", status_code=201)
def create_run(body: RunRequest, x_access_code: str | None = Header(default=None)) -> dict[str, str]:
    _check_access(x_access_code)
    if not settings.llm_enabled:
        raise HTTPException(status_code=503, detail="OPENROUTER_API_KEY is not configured on the server")
    options = {
        "fault": body.fault,
        "codegen_mode": body.codegen_mode or settings.codegen_mode,
    }
    try:
        run = manager.start(body.prompt, options)
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return {"run_id": run.id}


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    runs = sorted(manager.runs.values(), key=lambda r: r.created_at, reverse=True)
    return [{"run_id": r.id, "prompt": r.prompt, "status": r.status, "created_at": r.created_at} for r in runs[:20]]


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    snap = manager.snapshot(run_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="run not found")
    return snap


@app.get("/api/runs/{run_id}/events")
async def events(
    run_id: str,
    request: Request,
    after: int = -1,
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    """Server-sent events. Resume with the Last-Event-ID header or ?after=<seq>."""
    run = manager.runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found (or server restarted; fetch /api/runs/{id})")
    start = int(last_event_id) + 1 if last_event_id and last_event_id.isdigit() else after + 1

    async def stream():
        seq = start
        last_beat = time.monotonic()
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                return
            batch = run.events_since(seq)
            for ev in batch:
                yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev, default=str)}\n\n"
                seq = ev["seq"] + 1
            # Stop once the run has finished and every event is flushed.
            if not run.active and not batch and run.status in TERMINAL:
                return
            if time.monotonic() - last_beat > 15:
                yield ": heartbeat\n\n"  # keeps proxies (e.g. Railway's edge) from closing the stream
                last_beat = time.monotonic()
            await asyncio.sleep(0.2)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/runs/{run_id}/files/{path:path}", response_class=PlainTextResponse)
def get_file(run_id: str, path: str) -> str:
    content = manager.file(run_id, path)
    if content is None:
        raise HTTPException(status_code=404, detail="file not found")
    return content


@app.get("/api/runs/{run_id}/download")
def download(run_id: str) -> FileResponse:
    path = manager.artifact(run_id)
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="no artifact for this run")
    return FileResponse(path, media_type="application/zip", filename=Path(path).name)


@app.post("/api/runs/{run_id}/preview")
async def start_preview(run_id: str, x_access_code: str | None = Header(default=None)) -> dict[str, Any]:
    """Start the generated app as a live process, reachable at /preview/<run_id>/."""
    _check_access(x_access_code)
    snap = manager.snapshot(run_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="run not found")
    if snap["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="only a successfully validated app can be previewed")
    files = manager.files(run_id)
    try:
        p = await run_in_threadpool(previews.start, run_id, files)
    except PreviewError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"running": True, "base": f"/preview/{run_id}", "docs": f"/preview/{run_id}/docs", "started_at": p.started_at}


@app.get("/api/runs/{run_id}/preview")
def preview_status(run_id: str) -> dict[str, Any]:
    p = previews.get(run_id)
    return {"running": bool(p), "base": f"/preview/{run_id}", "docs": f"/preview/{run_id}/docs"}


_HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length", "upgrade"}


@app.api_route("/preview/{run_id}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def preview_proxy(run_id: str, path: str, request: Request) -> Response:
    """Reverse proxy to the generated app, so it is reachable on the platform's single public port."""
    p = previews.get(run_id)
    if p is None:
        raise HTTPException(status_code=404, detail="preview not running; start it from the Live App tab")
    forward = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "authorization", "accept")}
    try:
        upstream = await request.app.state.proxy.request(
            request.method,
            f"{p.base_url}/{path}",
            params=request.query_params,
            content=await request.body(),
            headers=forward,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"preview unreachable: {exc}") from exc
    headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_HEADERS}
    return Response(upstream.content, status_code=upstream.status_code, headers=headers)


# Serve the exported Next.js frontend from the same origin when it is present (Railway, Docker).
_frontend = Path(os.getenv("FRONTEND_DIR", Path(__file__).resolve().parents[2] / "frontend" / "out"))
if _frontend.is_dir():
    app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")
