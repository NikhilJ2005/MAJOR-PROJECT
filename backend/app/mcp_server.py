"""VibeStack as an MCP server, so AI assistants (Claude Desktop, Cursor, VS Code ...) can
generate validated backends as a tool call.

Served over streamable HTTP at /mcp/. Every call must carry the user's VibeStack login
token (`Authorization: Bearer <token>`), so runs belong to that user exactly as in the web UI.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .accounts import user_id_from_token
from .runs import RunManager
from .store import Store

INSTRUCTIONS = """VibeStack turns a plain-English description into a working backend (FastAPI, Flask or
Django). Agents analyse requirements, design the API, write code and tests, hunt for bugs and
self-heal until everything passes. Call generate_backend, then read files with read_file."""
MAX_WAIT_S = 15 * 60


def summary(snap: dict[str, Any]) -> dict[str, Any]:
    """The parts of a run an assistant needs to report back to its user."""
    spec = snap.get("spec") or {}
    bugs = snap.get("bugs") or []
    return {
        "run_id": snap["run_id"],
        "project_id": snap.get("project_id"),
        "version": snap.get("version"),
        "status": snap["status"],
        "stack": spec.get("stack"),
        "entities": [e["name"] for e in spec.get("entities", [])],
        "custom_actions": [f"{a['method']} {a['entity']} {a['path']}" for a in spec.get("actions", [])],
        "requirements": len(snap.get("requirements") or []),
        "repair_attempts": snap.get("iteration", 0),
        "bugs_found": [{"found_by": b["found_by"], "problem": b["detail"], "fixed": b["fixed"]} for b in bugs],
        "stop_reason": snap.get("stop_reason"),
        "files": len(snap.get("files") or []),
        "download": f"/api/runs/{snap['run_id']}/download" if snap.get("has_artifact") else None,
    }


def build_mcp(manager: RunManager, store: Store, start_run: Callable[..., str]) -> MCPServer:
    """Create the MCP server. `start_run(user, prompt, options)` is the same function the HTTP API uses."""
    server = MCPServer(name="VibeStack", instructions=INSTRUCTIONS, version="2.0.0")

    def user_from(ctx: Context) -> dict[str, Any]:
        header = (ctx.headers or {}).get("authorization", "")
        user_id = user_id_from_token(header[7:] if header.lower().startswith("bearer ") else None)
        user = store.user_by_id(user_id) if user_id is not None else None
        if user is None:
            raise ToolError("send your VibeStack login token as 'Authorization: Bearer <token>'")
        return user

    def owned(ctx: Context, run_id: str) -> dict[str, Any]:
        user = user_from(ctx)
        snap = manager.snapshot(run_id) if store.run_owner(run_id) == user["id"] else None
        if snap is None:
            raise ToolError(f"run {run_id} not found")
        return snap

    @server.tool()
    async def generate_backend(prompt: str, ctx: Context, stack: str = "fastapi", wait: bool = True) -> dict[str, Any]:
        """Generate, test and self-heal a backend from a description.

        stack: fastapi, flask or django. With wait=true (default) the call returns when the run has
        finished (usually 1-4 minutes); otherwise poll run_status.
        """
        user = user_from(ctx)
        try:
            run_id = start_run(user, prompt, {"stack": stack})
        except HTTPException as exc:  # quota, bad stack, too short: tell the assistant why
            raise ToolError(str(exc.detail)) from None
        return await finish(run_id, wait)

    async def finish(run_id: str, wait: bool) -> dict[str, Any]:
        waited = 0.0
        while wait and manager.runs[run_id].active and waited < MAX_WAIT_S:
            await asyncio.sleep(1.0)
            waited += 1.0
        return summary(manager.snapshot(run_id))

    @server.tool()
    async def edit_project(project_id: str, prompt: str, ctx: Context, wait: bool = True) -> dict[str, Any]:
        """Change an existing project ("add product reviews"); the agents build and validate the next version."""
        user = user_from(ctx)
        project = store.project(project_id, user["id"])
        if project is None or not project["current_run_id"]:
            raise ToolError(f"project {project_id} not found or has no working version yet")
        try:
            run_id = start_run(user, prompt, {"stack": project["stack"], "codegen_mode": "llm"},
                               project_id=project_id, base_run_id=project["current_run_id"])
        except HTTPException as exc:
            raise ToolError(str(exc.detail)) from None
        return await finish(run_id, wait)

    @server.tool()
    async def run_status(run_id: str, ctx: Context) -> dict[str, Any]:
        """Status and results of one of your runs."""
        return summary(owned(ctx, run_id))

    @server.tool()
    async def list_projects(ctx: Context) -> list[dict[str, Any]]:
        """Your projects with their current version."""
        user = user_from(ctx)
        return [{"project_id": p["id"], "name": p["name"], "stack": p["stack"], "versions": p["versions"],
                 "current_run_id": p["current_run_id"], "status": p["latest_status"]}
                for p in store.projects_for(user["id"])]

    @server.tool()
    async def list_files(run_id: str, ctx: Context) -> list[str]:
        """Every file path of a generated project."""
        return owned(ctx, run_id).get("files") or []

    @server.tool()
    async def read_file(run_id: str, path: str, ctx: Context) -> str:
        """The content of one generated file, e.g. app/routers/order.py or README.md."""
        owned(ctx, run_id)
        content = manager.file(run_id, path)
        if content is None:
            raise ToolError(f"no file {path} in run {run_id}")
        return content

    return server
