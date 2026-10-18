"""MCP servers as tools for the agents: bundled ones that run inside the container, and external ones.

The agents use them on their own (no Claude Desktop or other AI client involved):

    MCP_PRESETS=builtin,docs,kubernetes   # default: builtin (and kubernetes when a deploy cluster is set)
    MCP_SERVERS='[{"name": "docs", "url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ..."}},
                  {"name": "mine", "command": "my-mcp-server", "args": ["--stdio"]}]'

- builtin: VibeStack's own tool server (agent/mcp_builtin.py), started over stdio
- kubernetes: the open-source Kubernetes MCP server, read-only, for the deploy diagnoser
- docs: Context7 documentation search (HTTP)

Each agent gets its own set: the Reflector the code tools (builtin, docs, MCP_SERVERS), the deploy
diagnoser the cluster tools (kubernetes).

At the start of a repair we ask each server for its tools and wrap every one as a `Tool`,
named `<server>__<tool>`. A server that cannot be reached is skipped and logged; it never
breaks a run. The MCP client is asynchronous, so each request runs in a short-lived thread
with its own event loop, which works no matter where the graph is running.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .tools import Tool

log = logging.getLogger(__name__)
TIMEOUT_S = 30
CACHE_S = 300  # a server's tool list is asked for at most every 5 minutes
FOR_DEPLOY = {"kubernetes"}  # presets only the deploy diagnoser uses
_cache: dict[str, tuple[float, list[Any]]] = {}
_cache_lock = threading.Lock()


def _kubernetes_preset() -> dict[str, Any] | None:
    """The Kubernetes MCP server, read-only, on the deploy cluster (when it is installed and a cluster is set)."""
    binary = shutil.which(os.environ.get("KUBERNETES_MCP_SERVER", "kubernetes-mcp-server"))
    kubeconfig_b64 = os.environ.get("DEPLOY_KUBECONFIG_B64", "")
    if not binary or not (kubeconfig_b64 or os.environ.get("KUBERNETES_SERVICE_HOST")):
        return None
    folder = tempfile.mkdtemp(prefix="vibestack-k8s-mcp-")
    lines = ["read_only = true", 'toolsets = ["core"]']
    if kubeconfig_b64:
        path = os.path.join(folder, "kubeconfig")
        with open(path, "wb") as f:
            f.write(base64.b64decode(kubeconfig_b64))
        os.chmod(path, 0o600)
        lines.append(f'kubeconfig = "{path}"')
    config = os.path.join(folder, "config.toml")
    with open(config, "w") as f:
        f.write("\n".join(lines) + "\n")
    return {"name": "kubernetes", "command": binary, "args": ["--config", config]}


def presets() -> dict[str, dict[str, Any]]:
    """Well-known MCP servers that one word in MCP_PRESETS turns on (URLs can be overridden)."""
    docs_headers = {"CONTEXT7_API_KEY": os.environ["CONTEXT7_API_KEY"]} if os.environ.get("CONTEXT7_API_KEY") else {}
    known = {
        # VibeStack's own tools, in this container: docs of the installed libraries, versions, manifest checks.
        "builtin": {"name": "builtin", "command": sys.executable, "args": ["-m", "agent.mcp_builtin"]},
        # Current library documentation (FastAPI, Django, SQLAlchemy, Pydantic ...) for the repairs.
        "docs": {"name": "docs", "url": os.environ.get("MCP_DOCS_URL", "https://mcp.context7.com/mcp"), "headers": docs_headers},
    }
    kubernetes = _kubernetes_preset() if "kubernetes" in _preset_names() else None
    if kubernetes:
        known["kubernetes"] = kubernetes
    return known


def _preset_names() -> list[str]:
    raw = os.environ.get("MCP_PRESETS")
    if raw is None:  # default: VibeStack's own tools, and the cluster tools when deploys are set up
        return ["builtin", "kubernetes"]
    return [n.strip() for n in raw.split(",") if n.strip()]


def configured_servers(purpose: str = "repair") -> list[dict[str, Any]]:
    """The servers from MCP_PRESETS and MCP_SERVERS for one agent: "repair" (Reflector) or "deploy"."""
    known = presets()
    names = [n for n in _preset_names() if n in known and ((n in FOR_DEPLOY) == (purpose == "deploy"))]
    servers = [known[n] for n in names]
    if purpose == "deploy":
        return servers
    raw = os.environ.get("MCP_SERVERS", "").strip()
    if raw:
        try:
            servers += json.loads(raw)
        except json.JSONDecodeError:
            log.warning("MCP_SERVERS is not valid JSON; ignoring it")
    return [s for s in servers if isinstance(s, dict) and s.get("name") and (s.get("url") or s.get("server") or s.get("command"))]


def _run(coro_fn: Any, *args: Any) -> Any:
    """Run an async function to completion in a fresh thread and event loop."""
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(asyncio.run, coro_fn(*args)).result(timeout=TIMEOUT_S)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)  # a stuck server must not hold up the repair


def _client(server: dict[str, Any]) -> Any:
    """An MCP client for one server: a command (stdio), a URL (streamable HTTP) or, in tests, an in-process server."""
    from mcp import Client

    if server.get("server") is not None:
        return Client(server["server"])
    if server.get("command"):
        from mcp.client.stdio import StdioServerParameters

        # A scrubbed environment: the server never sees VibeStack's API keys.
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tempfile.gettempdir(),
               "PYTHONPATH": os.path.dirname(os.path.dirname(os.path.abspath(__file__))), **(server.get("env") or {})}
        return Client(StdioServerParameters(command=server["command"], args=server.get("args") or [], env=env))
    from mcp.client.streamable_http import streamable_http_client
    from mcp.shared._httpx_utils import create_mcp_http_client

    http = create_mcp_http_client(headers=server.get("headers") or {})
    return Client(streamable_http_client(server["url"], http_client=http))


async def _list(server: dict[str, Any]) -> list[Any]:
    async with _client(server) as client:
        return list((await client.list_tools()).tools)


async def _call(server: dict[str, Any], name: str, args: dict[str, Any]) -> str:
    async with _client(server) as client:
        result = await client.call_tool(name, args)
    text = "\n".join(getattr(block, "text", "") or str(block) for block in result.content)
    return f"error: {text}" if result.is_error else text


def _wrap(server: dict[str, Any], remote: Any) -> Tool:
    """One remote MCP tool as a local Tool the Reflector can call."""

    def call(**args: Any) -> str:
        return _run(_call, server, remote.name, args)

    schema = remote.input_schema or {"type": "object", "properties": {}}
    name = re.sub(r"[^A-Za-z0-9_-]", "_", f"{server['name']}__{remote.name}")[:64]  # what providers accept
    return Tool(name, remote.description or remote.name, schema, call)


def _tool_list(server: dict[str, Any]) -> list[Any]:
    """A server's tools, asked for at most every CACHE_S seconds (each repair would otherwise ask again)."""
    key = json.dumps({k: v for k, v in server.items() if k != "server"}, sort_keys=True, default=str)
    if server.get("server") is None:
        with _cache_lock:
            hit = _cache.get(key)
            if hit and time.monotonic() - hit[0] < CACHE_S:
                return hit[1]
    remote = _run(_list, server)
    if server.get("server") is None:
        with _cache_lock:
            _cache[key] = (time.monotonic(), remote)
    return remote


def load_tools(servers: list[dict[str, Any]] | None = None, purpose: str = "repair") -> list[Tool]:
    """Every tool of the MCP servers configured for one agent ("repair" or "deploy")."""
    tools: list[Tool] = []
    for server in configured_servers(purpose) if servers is None else servers:
        try:
            tools += [_wrap(server, t) for t in _tool_list(server)]
        except Exception as exc:  # noqa: BLE001 - an unreachable server is skipped, never fatal
            log.warning("MCP server %s skipped: %s", server.get("name"), exc)
    return tools
