"""VibeStack HTTP API: accounts, runs, live progress (SSE), files, downloads and live previews.

Every run belongs to the user who started it; other users get 404 for it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from agent import plugins
from agent.config import settings
from agent.stacks import STACKS

from .accounts import create_token, hash_password, user_id_from_token, verify_password
from .mcp_server import build_mcp
from .preview import PreviewError, PreviewManager
from .runs import TERMINAL, RunManager
from .store import IntegrityError, Store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vibestack")

previews = PreviewManager()


@asynccontextmanager
async def lifespan(_: FastAPI):
    app.state.proxy = httpx.AsyncClient(timeout=30, trust_env=False)
    # A fresh MCP server per lifespan: its session manager can only run once.
    mcp = build_mcp(manager, store, start_run)
    app.state.mcp_app = mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        # Every tool call needs a Bearer token, which a browser cannot add cross-site, so
        # DNS-rebinding protection (a localhost-only default) is not needed behind a public domain.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    async with mcp.session_manager.run():
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

store = Store(settings.platform_database_url)
manager = RunManager(store)


# --------------------------------------------------------------------------- accounts


class Credentials(BaseModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=255)
    password: str = Field(min_length=8, max_length=128)
    invite_code: str | None = None


def current_user(authorization: str | None = Header(default=None), token: str | None = None) -> dict[str, Any]:
    """The logged-in user. The token comes from `Authorization: Bearer ...`, or `?token=` for
    links the browser opens itself (event stream, downloads)."""
    raw = authorization[7:] if authorization and authorization.startswith("Bearer ") else token
    user_id = user_id_from_token(raw)
    user = store.user_by_id(user_id) if user_id is not None else None
    if user is None:
        raise HTTPException(status_code=401, detail="please log in", headers={"WWW-Authenticate": "Bearer"})
    user["is_admin"] = user["email"] in settings.admin_emails
    return user


CurrentUser = Annotated[dict[str, Any], Depends(current_user)]


def own_run(run_id: str, user: dict[str, Any]) -> None:
    """404 unless this run belongs to the user (other people's runs look like they don't exist)."""
    if store.run_owner(run_id) != user["id"]:
        raise HTTPException(status_code=404, detail="run not found")


@app.post("/api/auth/register", status_code=201)
def register(body: Credentials) -> dict[str, Any]:
    if settings.access_code and body.invite_code != settings.access_code:
        raise HTTPException(status_code=403, detail="a valid invite code is required")
    email = body.email.lower()
    try:
        user_id = store.add_user(email, hash_password(body.password))
    except IntegrityError:
        raise HTTPException(status_code=409, detail="email already registered") from None
    return {"token": create_token(user_id), "email": email}


@app.post("/api/auth/login")
def login(body: Credentials) -> dict[str, Any]:
    user = store.user_by_email(body.email.lower())
    if user is None or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="wrong email or password")
    return {"token": create_token(user["id"]), "email": user["email"]}


@app.get("/api/auth/me")
def me(user: CurrentUser) -> dict[str, Any]:
    return {"id": user["id"], "email": user["email"], "is_admin": user["is_admin"]}


# --------------------------------------------------------------------------- runs


class RunRequest(BaseModel):
    prompt: str = Field(min_length=10, max_length=6000)
    stack: Literal["fastapi", "flask", "django"] = "fastapi"
    codegen_mode: str | None = Field(default=None, pattern="^(llm|template)$")
    plugins: list[str] | None = None  # None: the server's default plugins


class Message(BaseModel):
    prompt: str = Field(min_length=3, max_length=6000)


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
        "invite_required": bool(settings.access_code),
        "max_heal_safety": settings.max_heal_safety,
        "max_runs_per_day": settings.max_runs_per_day,
        "stacks": [{"name": s.name, "label": s.label} for s in STACKS.values()],
        "plugins": plugins.describe(),
        "provider": settings.llm_provider,
        "models": {"strong": settings.strong_model, "cheap": settings.cheap_model, "fallback": settings.fallback_model},
    }


def start_run(
    user: dict[str, Any], prompt: str, options: dict[str, Any], project_id: str | None = None, base_run_id: str | None = None
) -> str:
    """Checks and starts a run for a user. Shared by the HTTP API and the MCP server.

    Without a project_id a new project is created, so every run belongs to a project's history.
    """
    if not settings.llm_enabled:
        raise HTTPException(status_code=503, detail="no LLM API key is configured on the server")
    if not 10 <= len(prompt) <= 6000:
        raise HTTPException(status_code=422, detail="the description must be 10-6000 characters")
    if options.get("stack", "fastapi") not in STACKS:
        raise HTTPException(status_code=422, detail=f"stack must be one of {', '.join(STACKS)}")
    is_admin = user.get("is_admin") or user["email"] in settings.admin_emails
    if store.runs_since(user["id"], time.time() - 86400) >= settings.max_runs_per_day and not is_admin:
        raise HTTPException(status_code=429, detail=f"daily limit of {settings.max_runs_per_day} runs reached")
    options = {"codegen_mode": settings.codegen_mode, **options}
    if project_id is None:
        project_id = store.add_project(user["id"], " ".join(prompt.split()[:6]), options.get("stack", "fastapi"))
    elif manager.project_busy(project_id):
        raise HTTPException(status_code=409, detail="this project is already being built; wait for it to finish")
    try:
        return manager.start(prompt, options, owner_id=user["id"], project_id=project_id, base_run_id=base_run_id).id
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


def _options(body: RunRequest) -> dict[str, Any]:
    options: dict[str, Any] = {"stack": body.stack, "codegen_mode": body.codegen_mode or settings.codegen_mode}
    if body.plugins is not None:
        unknown = set(body.plugins) - {p["name"] for p in plugins.describe()}
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown plugins: {', '.join(sorted(unknown))}")
        options["plugins"] = body.plugins
    return options


@app.post("/api/runs", status_code=201)
def create_run(body: RunRequest, user: CurrentUser) -> dict[str, str]:
    run_id = start_run(user, body.prompt, _options(body))
    return {"run_id": run_id, "project_id": store.run(run_id)["project_id"]}


# --------------------------------------------------------------------------- projects (Lovable-style sessions)


def own_project(project_id: str, user: dict[str, Any]) -> dict[str, Any]:
    project = store.project(project_id, user["id"])
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@app.post("/api/projects", status_code=201)
def create_project(body: RunRequest, user: CurrentUser) -> dict[str, str]:
    """A new project: the first prompt builds version 1."""
    return create_run(body, user)


@app.get("/api/projects")
def list_projects(user: CurrentUser) -> list[dict[str, Any]]:
    return store.projects_for(user["id"])


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, user: CurrentUser) -> dict[str, Any]:
    """The project, its versions (the chat history) and which version is current."""
    project = own_project(project_id, user)
    return {**project, "versions": store.versions(project_id), "busy": manager.project_busy(project_id)}


@app.post("/api/projects/{project_id}/messages", status_code=201)
def send_message(project_id: str, body: Message, user: CurrentUser) -> dict[str, str]:
    """A follow-up prompt: the agents change the current version and validate the result as version N+1."""
    project = own_project(project_id, user)
    if manager.project_busy(project_id):
        raise HTTPException(status_code=409, detail="the agents are still working on this project")
    # Start from the working version; with none yet, from the latest attempt (an edit or "try again").
    base = project["current_run_id"] or next((v["run_id"] for v in reversed(store.versions(project_id))), None)
    if base is None:
        raise HTTPException(status_code=409, detail="the project has no version yet")
    options = {"stack": project["stack"], "codegen_mode": "llm"}
    run_id = start_run(user, body.prompt, options, project_id=project_id, base_run_id=base)
    return {"run_id": run_id, "project_id": project_id}


class Restore(BaseModel):
    run_id: str


@app.post("/api/projects/{project_id}/current")
def restore_version(project_id: str, body: Restore, user: CurrentUser) -> dict[str, Any]:
    """Make an earlier working version the current one (the next edit starts from it)."""
    own_project(project_id, user)
    run = store.run(body.run_id)
    if run is None or run["project_id"] != project_id or run["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="only a working version of this project can be restored")
    store.set_current(project_id, body.run_id)
    return {"current_run_id": body.run_id}


@app.get("/api/runs")
def list_runs(user: CurrentUser) -> list[dict[str, Any]]:
    return [
        {"run_id": r["id"], "prompt": r["prompt"], "stack": r["stack"], "status": r["status"], "created_at": r["created_at"]}
        for r in store.runs_for(user["id"])
    ]


@app.get("/api/stats")
def stats(user: CurrentUser) -> dict[str, Any]:
    """Headline numbers: success rate, repairs, bugs found and fixed, cost. Admins also see everyone's."""
    out: dict[str, Any] = {"mine": store.stats(user["id"])}
    if user["is_admin"]:
        out["everyone"] = store.stats()
    return out


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, user: CurrentUser) -> dict[str, Any]:
    own_run(run_id, user)
    snap = manager.snapshot(run_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="run not found")
    return snap


@app.get("/api/runs/{run_id}/events")
async def events(
    run_id: str,
    request: Request,
    user: CurrentUser,
    after: int = -1,
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    """Server-sent events. Resume with the Last-Event-ID header or ?after=<seq>."""
    own_run(run_id, user)
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
def get_file(run_id: str, path: str, user: CurrentUser) -> str:
    own_run(run_id, user)
    content = manager.file(run_id, path)
    if content is None:
        raise HTTPException(status_code=404, detail="file not found")
    return content


@app.get("/api/runs/{run_id}/download")
def download(run_id: str, user: CurrentUser) -> FileResponse:
    own_run(run_id, user)
    path = manager.artifact(run_id)
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="no artifact for this run")
    return FileResponse(path, media_type="application/zip", filename=Path(path).name)


def _preview_info(p: Any) -> dict[str, Any]:
    if p is None:
        return {"running": False, "base": None, "docs": None}
    return {"running": True, "base": p.public_path, "docs": f"{p.public_path}/docs", "started_at": p.started_at}


@app.post("/api/runs/{run_id}/preview")
async def start_preview(run_id: str, user: CurrentUser) -> dict[str, Any]:
    """Start the generated app as a live process, reachable at a private /preview/<key>/ URL."""
    own_run(run_id, user)
    snap = manager.snapshot(run_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="run not found")
    if snap["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="only a successfully validated app can be previewed")
    files = manager.files(run_id)
    stack = (snap.get("spec") or {}).get("stack", "fastapi")
    try:
        p = await run_in_threadpool(previews.start, run_id, files, stack)
    except PreviewError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _preview_info(p)


@app.get("/api/runs/{run_id}/preview")
def preview_status(run_id: str, user: CurrentUser) -> dict[str, Any]:
    own_run(run_id, user)
    return _preview_info(previews.get(run_id))


_HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length", "upgrade"}


@app.api_route("/preview/{key}/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def preview_proxy(key: str, path: str, request: Request) -> Response:
    """Reverse proxy to the generated app, so it is reachable on the platform's single public port.

    Responses are streamed, so the generated app's live updates (Server-Sent Events) work too.
    """
    p = previews.by_key(key)
    if p is None:
        raise HTTPException(status_code=404, detail="preview not running; start it from the Live App tab")
    forward = {k: v for k, v in request.headers.items() if k.lower() in ("content-type", "authorization", "accept")}
    proxy: httpx.AsyncClient = request.app.state.proxy
    outgoing = proxy.build_request(
        request.method, f"{p.base_url}/{path}", params=request.query_params, content=await request.body(), headers=forward
    )
    try:
        upstream = await proxy.send(outgoing, stream=True)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"preview unreachable: {exc}") from exc
    headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_HEADERS}
    return StreamingResponse(upstream.aiter_bytes(), status_code=upstream.status_code, headers=headers,
                             background=BackgroundTask(upstream.aclose))


async def mcp_endpoint(scope, receive, send) -> None:
    """Forward /mcp/ to the MCP app created in lifespan()."""
    await app.state.mcp_app(scope, receive, send)


app.mount("/mcp", mcp_endpoint)


# Serve the exported Next.js frontend from the same origin when it is present (Railway, Docker).
_frontend = Path(os.getenv("FRONTEND_DIR", Path(__file__).resolve().parents[2] / "frontend" / "out"))


def frontend_is_stale(out: Path) -> bool:
    """True when the exported UI is older than the frontend source next to it (a local run after git pull)."""
    index = out / "index.html"
    sources = [p for d in ("app", "components", "lib") for p in (out.parent / d).rglob("*") if p.is_file()]
    return index.is_file() and bool(sources) and max(p.stat().st_mtime for p in sources) > index.stat().st_mtime


class UIFiles(StaticFiles):
    """The exported UI. Pages are re-checked on every visit, so a new deploy shows up at once;
    the hashed files in /_next/static never change, so browsers may keep them for a year."""

    async def get_response(self, path: str, scope: Any) -> Any:
        response = await super().get_response(path, scope)
        immutable = path.startswith("_next/static/")
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if immutable else "no-cache"
        return response


if _frontend.is_dir() and frontend_is_stale(_frontend):
    log.warning("%s is older than the frontend source: run `npm run build` in frontend/ to see the current UI", _frontend)
if _frontend.is_dir():
    app.mount("/", UIFiles(directory=_frontend, html=True), name="frontend")
