"""VibeStack's MCP endpoint, exercised with the official MCP client over real HTTP."""

import asyncio
import threading
import time
import uuid

import httpx2
import uvicorn
from fastapi.testclient import TestClient
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from agent.sandbox import free_port


def _serve(app):
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    return server, thread, port


def _token(app) -> str:
    with TestClient(app) as client:
        email = f"mcp-{uuid.uuid4().hex[:8]}@example.com"
        return client.post("/api/auth/register", json={"email": email, "password": "long-enough-pw"}).json()["token"]


async def _call(port, token, tool, args):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=300) as http,
        Client(streamable_http_client(f"http://127.0.0.1:{port}/mcp/", http_client=http)) as client,
    ):
        tools = sorted(t.name for t in (await client.list_tools()).tools)
        return tools, await client.call_tool(tool, args)


def test_generate_backend_through_mcp(fake_llm):
    from app.main import app

    token = _token(app)
    server, thread, port = _serve(app)
    try:
        tools, result = asyncio.run(_call(port, token, "generate_backend", {"prompt": "A blog with posts and comments", "stack": "flask"}))
        assert tools == ["generate_backend", "list_files", "list_runs", "read_file", "run_status"]
        assert not result.is_error, result
        data = result.structured_content
        assert data["status"] == "succeeded" and data["stack"] == "flask"
        assert data["entities"] == ["Post", "Comment"]

        _, readme = asyncio.run(_call(port, token, "read_file", {"run_id": data["run_id"], "path": "README.md"}))
        assert "Flask" in readme.content[0].text

        _, denied = asyncio.run(_call(port, None, "list_runs", {}))
        assert denied.is_error and "Bearer" in denied.content[0].text
    finally:
        server.should_exit = True
        thread.join(timeout=10)
