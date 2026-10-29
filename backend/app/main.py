"""VibeStack HTTP API: accounts, runs, live progress (SSE), files, downloads and live previews.

Every run belongs to the user who started it; other users get 404 for it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from agent import blueprints, plugins, storage
from agent.config import settings
from agent.stacks import STACKS
from agent.state import ProjectSpec

from . import deploy
from .accounts import create_token, hash_password, user_id_from_token, verify_password
from .mcp_server import build_mcp
from .ops import (
    Idempotency,
    RateLimiter,
    RequestContext,
    client_ip,
    metrics_text,
    setup_logging,
    setup_sentry,
)
from .preview import PreviewError, PreviewManager
from .queue import make_queue
from .runs import TERMINAL, RunManager
from .store import IntegrityError, Store

setup_logging()
setup_sentry()
log = logging.getLogger("vibestack")

previews = PreviewManager()


@asynccontextmanager
async def lifespan(_: FastAPI):
    app.state.proxy = httpx.AsyncClient(timeout=30, trust_env=False)
    # A fresh MCP server per lifespan: its session manager can only run once.
    mcp = build_mcp(manager, store, start_run, preview=_preview_path, deploy=deploy_project, deployment=get_deployment)
    app.state.mcp_app = mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        # Every tool call needs a Bearer token, which a browser cannot add cross-site, so
        # DNS-rebinding protection (a localhost-only default) is not needed behind a public domain.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    manager.recover()  # builds a restart or redeploy interrupted continue from their last checkpoint
    _start_maintenance()
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
app.add_middleware(RequestContext)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o],
    allow_methods=["*"],
    allow_headers=["*"],
)

store = Store(settings.platform_database_url)
manager = RunManager(store, queue=make_queue())
deployer = deploy.Deployer(store, files=manager.files, spec=lambda run_id: (manager.snapshot(run_id) or {}).get("spec"))


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


# Per IP: slows down password guessing (AUTH_RATE_LIMIT attempts per minute).
_shared = manager.queue.r if manager.queue is not None else None  # Redis, when the build queue uses it
auth_limit = RateLimiter(limit=int(os.getenv("AUTH_RATE_LIMIT", "20")), seconds=60, redis=_shared)
idempotency = Idempotency(redis=_shared)


def check_auth_rate(request: Request) -> None:
    if not auth_limit.allow(client_ip(request)):
        raise HTTPException(status_code=429, detail="too many attempts; wait a minute and try again")


@app.post("/api/auth/register", status_code=201)
def register(body: Credentials, request: Request) -> dict[str, Any]:
    check_auth_rate(request)
    if settings.access_code and body.invite_code != settings.access_code:
        raise HTTPException(status_code=403, detail="a valid invite code is required")
    email = body.email.lower()
    try:
        user_id = store.add_user(email, hash_password(body.password))
    except IntegrityError:
        raise HTTPException(status_code=409, detail="email already registered") from None
    return {"token": create_token(user_id), "email": email}


@app.post("/api/auth/login")
def login(body: Credentials, request: Request) -> dict[str, Any]:
    check_auth_rate(request)
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
    plugins: list[str] | None = None  # plugins forced on (the agents choose the rest)
    plugins_off: list[str] | None = None  # plugins forced off
    auto_plugins: bool = True  # let the Architect and Planner choose plugins
    review_design: bool = False  # wait for the user to approve the design before any code is written
    instructions: str | None = Field(default=None, max_length=4000)  # project rules every agent follows
    blueprint: str | None = Field(default=None, max_length=40)  # build a built-in blueprint (agent/blueprints)


class Message(BaseModel):
    prompt: str = Field(min_length=3, max_length=6000)
    # "edit": change the app as the prompt says. "fix": keep healing the latest version's code as it is.
    mode: Literal["edit", "fix"] = "edit"
    review_design: bool = False


class Approval(BaseModel):
    """The design review: approve as it is, approve an edited design, or ask for a change in words."""

    spec: dict[str, Any] | None = None
    instruction: str | None = Field(default=None, min_length=3, max_length=2000)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/ready")
def ready() -> dict[str, Any]:
    """Everything a build needs answers: the database, Redis (with workers) and the bucket (with S3)."""
    checks: dict[str, str] = {}
    try:
        store.ping()
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        checks["database"] = f"down: {type(exc).__name__}"
    if manager.queue is not None:
        try:
            manager.queue.r.ping()
            checks["redis"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = f"down: {type(exc).__name__}"
    if storage.bucket():
        try:
            storage.check()
            checks["storage"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["storage"] = f"down: {type(exc).__name__}"
    if any(v != "ok" for v in checks.values()):
        raise HTTPException(status_code=503, detail=checks)
    return {"status": "ready", **checks}


@app.get("/api/metrics", response_class=PlainTextResponse)
def metrics(authorization: str | None = Header(default=None)) -> str:
    """Prometheus metrics. Send `Authorization: Bearer <METRICS_TOKEN>`, or an admin's login token."""
    raw = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    expected = os.getenv("METRICS_TOKEN", "")
    user_id = user_id_from_token(raw)
    user = store.user_by_id(user_id) if user_id is not None else None
    if not ((expected and raw == expected) or (user and user["email"] in settings.admin_emails)):
        raise HTTPException(status_code=401, detail="metrics need METRICS_TOKEN or an admin login")
    queued = manager.queue.waiting() if manager.queue is not None else None
    return metrics_text(store.run_metrics(), manager.active_count(), previews.count(), queued)


@app.get("/api/blueprints")
def list_blueprints() -> list[dict[str, Any]]:
    """Built-in designs of complex apps that build with verified code only (no repairs needed)."""
    return blueprints.summaries()


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
        "deploy_enabled": deploy.enabled(),
        "deploy_domain": settings.deploy_domain if deploy.enabled() else None,
        "provider": settings.llm_provider,
        "models": {"strong": settings.strong_model, "cheap": settings.cheap_model, "fallback": settings.fallback_model},
    }


def start_run(
    user: dict[str, Any], prompt: str, options: dict[str, Any], project_id: str | None = None, base_run_id: str | None = None,
    spec: dict[str, Any] | None = None,
) -> str:
    """Checks and starts a run for a user. Shared by the HTTP API and the MCP server.

    Without a project_id a new project is created, so every run belongs to a project's history.
    """
    if not settings.llm_enabled and spec is None:  # a blueprint's design needs no model to build
        raise HTTPException(status_code=503, detail="no LLM API key is configured on the server")
    if not 10 <= len(prompt) <= 6000:
        raise HTTPException(status_code=422, detail="the description must be 10-6000 characters")
    if options.get("stack", "fastapi") not in STACKS:
        raise HTTPException(status_code=422, detail=f"stack must be one of {', '.join(STACKS)}")
    is_admin = user.get("is_admin") or user["email"] in settings.admin_emails
    if store.runs_since(user["id"], time.time() - 86400) >= settings.max_runs_per_day and not is_admin:
        raise HTTPException(status_code=429, detail=f"daily limit of {settings.max_runs_per_day} runs reached")
    if store.running_for(user["id"]) >= settings.max_active_runs_per_user and not is_admin:
        raise HTTPException(status_code=429, detail=f"you already have {settings.max_active_runs_per_user} builds running; "
                                                     "wait for one to finish")
    if manager.queue is not None and manager.queue.waiting() >= settings.queue_max:
        raise HTTPException(status_code=503, detail="every builder is busy; try again in a minute",
                            headers={"Retry-After": "60"})
    options = {"codegen_mode": settings.codegen_mode, **options}
    if project_id is None:
        project_id = store.add_project(user["id"], " ".join(prompt.split()[:6]), options.get("stack", "fastapi"))
        if options.get("instructions"):
            store.set_instructions(project_id, options["instructions"])
    elif manager.project_busy(project_id):
        raise HTTPException(status_code=409, detail="this project is already being built; wait for it to finish")
    rules = (store.project(project_id, user["id"]) or {}).get("instructions")
    options = {**options, "instructions": rules} if rules else {k: v for k, v in options.items() if k != "instructions"}
    try:
        return manager.start(prompt, options, owner_id=user["id"], spec=spec, project_id=project_id,
                             base_run_id=base_run_id).id
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


def _options(body: RunRequest) -> dict[str, Any]:
    options: dict[str, Any] = {"stack": body.stack, "codegen_mode": body.codegen_mode or settings.codegen_mode}
    known = {p["name"] for p in plugins.describe()}
    for key in ("plugins", "plugins_off"):
        names = getattr(body, key)
        if names is None:
            continue
        unknown = set(names) - known
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown plugins: {', '.join(sorted(unknown))}")
        options[key] = names
    options["auto_plugins"] = body.auto_plugins
    options["review_design"] = body.review_design
    if body.instructions:
        options["instructions"] = body.instructions
    return options


@app.post("/api/runs/{run_id}/cancel")
def cancel_run(run_id: str, user: CurrentUser) -> dict[str, str]:
    """Stop a build: it ends after the agent working now and keeps the last finished step."""
    own_run(run_id, user)
    try:
        manager.cancel(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "stopping"}


@app.post("/api/runs/{run_id}/approve")
def approve_design(run_id: str, body: Approval, user: CurrentUser) -> dict[str, str]:
    """Continue a run that waits at the design review."""
    own_run(run_id, user)
    decision: dict[str, Any] = {"approved": True}  # never empty: LangGraph reads {} as "no resume values"
    if body.instruction:
        decision["instruction"] = body.instruction.strip()
    elif body.spec is not None:
        try:
            decision["spec"] = ProjectSpec.model_validate(body.spec).model_dump()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"the edited design is not valid: {exc}") from exc
    try:
        manager.approve(run_id, decision)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"status": "continuing"}


@app.post("/api/runs", status_code=201)
def create_run(body: RunRequest, user: CurrentUser,
               idempotency_key: str | None = Header(default=None, max_length=100)) -> dict[str, str]:
    """Start a build. With an Idempotency-Key header, sending the same request again returns the same build."""
    key = f"{user['id']}:{idempotency_key}" if idempotency_key else None
    if key and (earlier := idempotency.get(key)):
        return earlier
    spec = None
    if body.blueprint:
        bp = blueprints.get(body.blueprint)
        if bp is None:
            raise HTTPException(status_code=404, detail="unknown blueprint")
        spec = {**bp["spec"], "stack": body.stack}
    run_id = start_run(user, body.prompt, _options(body), spec=spec)
    result = {"run_id": run_id, "project_id": store.run(run_id)["project_id"]}
    if key:
        idempotency.put(key, result)
    return result


# --------------------------------------------------------------------------- projects (Lovable-style sessions)


def own_project(project_id: str, user: dict[str, Any]) -> dict[str, Any]:
    project = store.project(project_id, user["id"])
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


class ProjectRules(BaseModel):
    instructions: str = Field(default="", max_length=4000)


@app.patch("/api/projects/{project_id}")
def update_project(project_id: str, body: ProjectRules, user: CurrentUser) -> dict[str, Any]:
    """Project rules (like a knowledge file): every agent follows them on every later build of this project."""
    own_project(project_id, user)
    store.set_instructions(project_id, body.instructions)
    return own_project(project_id, user)


@app.post("/api/projects", status_code=201)
def create_project(body: RunRequest, user: CurrentUser,
                   idempotency_key: str | None = Header(default=None, max_length=100)) -> dict[str, str]:
    """A new project: the first prompt builds version 1."""
    return create_run(body, user, idempotency_key)


@app.get("/api/projects")
def list_projects(user: CurrentUser) -> list[dict[str, Any]]:
    return store.projects_for(user["id"])


# --------------------------------------------------------------------------- one-click deploy (app/deploy.py)


def _start_maintenance() -> None:
    """Every 10 minutes: tidy deployments (interrupted ones, expired demo apps) and apply RETENTION_DAYS."""
    def loop() -> None:
        while True:
            try:
                if deploy.enabled():
                    deployer.sweep()
                if settings.retention_days > 0:
                    manager.prune(settings.retention_days)
            except Exception:
                log.exception("maintenance failed")
            time.sleep(600)

    threading.Thread(target=loop, daemon=True, name="maintenance").start()


def _deployment_view(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: row[k] for k in ("run_id", "namespace", "url", "status", "message", "created_at", "updated_at")} | {
        "steps": json.loads(row["steps"] or "[]")}


@app.post("/api/projects/{project_id}/deploy", status_code=202)
def deploy_project(project_id: str, user: CurrentUser) -> dict[str, Any]:
    """Put the project's current version live on the Kubernetes cluster (or update it)."""
    if not deploy.enabled():
        raise HTTPException(status_code=503, detail="deploying is not set up on this server (see docs/DEPLOY.md)")
    project = own_project(project_id, user)
    run_id = project["current_run_id"]
    record = store.run(run_id) if run_id else None
    if not record or record["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="only a version that passed all its tests can be deployed")
    try:
        return _deployment_view(deployer.start(project, run_id, user["id"]))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/deployment")
def get_deployment(project_id: str, user: CurrentUser) -> dict[str, Any] | None:
    own_project(project_id, user)
    return _deployment_view(store.deployment(project_id))


@app.delete("/api/projects/{project_id}/deployment", status_code=204)
def undeploy_project(project_id: str, user: CurrentUser) -> Response:
    own_project(project_id, user)
    try:
        deployer.undeploy(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return Response(status_code=204)


@app.get("/api/deployments/{project_id}/code", include_in_schema=False)
def deployment_code(project_id: str, token: str) -> Response:
    """The deployed version's code, fetched by the app's pods (the secret token is the only key)."""
    data = deployer.code_zip(project_id, token)
    if data is None:
        raise HTTPException(status_code=404, detail="not found")
    return Response(data, media_type="application/zip")


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
    latest = next((v["run_id"] for v in reversed(store.versions(project_id))), None)
    # An edit starts from the working version (with none yet, from the latest attempt);
    # "keep fixing" continues healing the latest version's own code.
    base = latest if body.mode == "fix" else project["current_run_id"] or latest
    if base is None:
        raise HTTPException(status_code=409, detail="the project has no version yet")
    options = {"stack": project["stack"], "codegen_mode": "llm", "resume": body.mode == "fix",
               "review_design": body.review_design and body.mode == "edit"}
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
    run = manager.get_run(run_id)
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
def download(run_id: str, user: CurrentUser) -> Response:
    own_run(run_id, user)
    path = manager.artifact(run_id)
    link = storage.download_link(path) if path else None
    if link:  # kept in an S3-compatible bucket: a short-lived signed link
        return RedirectResponse(link)
    if not path or not Path(path).exists():
        raise HTTPException(status_code=404, detail="no artifact for this run")
    return FileResponse(path, media_type="application/zip", filename=Path(path).name)


def _preview_info(p: Any) -> dict[str, Any]:
    if p is None:
        return {"running": False, "base": None, "docs": None}
    return {"running": True, "base": p.public_path, "docs": f"{p.public_path}/docs", "started_at": p.started_at}


@app.post("/api/runs/{run_id}/preview")
async def start_preview(run_id: str, user: CurrentUser, draft: bool = False) -> dict[str, Any]:
    """Start the generated app as a live process, reachable at a private /preview/<key>/ URL.

    draft=true starts the verified template build instead: available while the agents are still
    working (or after a failed build), with custom actions not written yet.
    """
    own_run(run_id, user)
    snap = manager.snapshot(run_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="run not found")
    if draft:
        files, key = manager.draft_files(run_id), f"{run_id}-draft"
        if not files:
            raise HTTPException(status_code=409, detail="the draft is not ready yet")
    elif snap["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="only a successfully validated app can be previewed")
    else:
        files, key = manager.files(run_id), run_id
        previews.stop(f"{run_id}-draft")  # the finished app replaces its draft
    stack = (snap.get("spec") or {}).get("stack", "fastapi")
    try:
        p = await run_in_threadpool(previews.start, key, files, stack)
    except PreviewError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _preview_info(p)


def _preview_path(run_id: str) -> str:
    """Start (or reuse) the live preview of a successful run; its private path (used by MCP)."""
    running = previews.get(run_id)
    if running:
        return running.public_path
    snap = manager.snapshot(run_id) or {}
    try:
        return previews.start(run_id, manager.files(run_id), (snap.get("spec") or {}).get("stack", "fastapi")).public_path
    except PreviewError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/runs/{run_id}/preview")
def preview_status(run_id: str, user: CurrentUser, draft: bool = False) -> dict[str, Any]:
    own_run(run_id, user)
    return _preview_info(previews.get(f"{run_id}-draft" if draft else run_id))


_HOP_HEADERS = {"connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length", "upgrade"}
# The generated app only allows framing by its own site; the workspace shows it in an iframe, so the
# preview drops those two (the deployed app keeps them).
_PREVIEW_DROPPED = _HOP_HEADERS | {"x-frame-options", "content-security-policy"}


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
    headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _PREVIEW_DROPPED}
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
