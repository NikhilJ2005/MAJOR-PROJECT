"""External MCP servers as tools for the Reflector.

Any MCP server (documentation search, a company's internal API catalogue, a linter service ...)
can lend its tools to the agents. List the servers in the MCP_SERVERS environment variable:

    MCP_PRESETS=docs,github            # well-known servers by name (see presets)
    MCP_SERVERS='[{"name": "docs", "url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ..."}}]'

At the start of a repair we ask each server for its tools and wrap every one as a `Tool`,
named `<server>__<tool>`. A server that cannot be reached is skipped and logged; it never
breaks a run. The MCP client is asynchronous, so each request runs in a short-lived thread
with its own event loop, which works no matter where the graph is running.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .tools import Tool

log = logging.getLogger(__name__)
TIMEOUT_S = 30


def presets() -> dict[str, dict[str, Any]]:
    """Well-known MCP servers that one word in MCP_PRESETS turns on (URLs can be overridden)."""
    docs_headers = {"CONTEXT7_API_KEY": os.environ["CONTEXT7_API_KEY"]} if os.environ.get("CONTEXT7_API_KEY") else {}
    github_token = os.environ.get("GITHUB_MCP_TOKEN", "")
    return {
        # Current library documentation (FastAPI, Django, SQLAlchemy, Pydantic ...) for the repairs.
        "docs": {"name": "docs", "url": os.environ.get("MCP_DOCS_URL", "https://mcp.context7.com/mcp"), "headers": docs_headers},
        # GitHub's own MCP server: repositories, Actions runs and logs (a read-only token is enough).
        "github": {"name": "github", "url": os.environ.get("MCP_GITHUB_URL", "https://api.githubcopilot.com/mcp/"),
                   "headers": {"Authorization": f"Bearer {github_token}"} if github_token else {}},
    }


def configured_servers() -> list[dict[str, Any]]:
    """The servers from MCP_PRESETS (e.g. "docs,github") and MCP_SERVERS (a JSON list)."""
    known = presets()
    servers = [known[name.strip()] for name in os.environ.get("MCP_PRESETS", "").split(",") if name.strip() in known]
    raw = os.environ.get("MCP_SERVERS", "").strip()
    if raw:
        try:
            servers += json.loads(raw)
        except json.JSONDecodeError:
            log.warning("MCP_SERVERS is not valid JSON; ignoring it")
    return [s for s in servers if isinstance(s, dict) and s.get("name") and (s.get("url") or s.get("server"))]


def _run(coro_fn: Any, *args: Any) -> Any:
    """Run an async function to completion in a fresh thread and event loop."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro_fn(*args)).result(timeout=TIMEOUT_S)


def _client(server: dict[str, Any]) -> Any:
    """An MCP client for one server: a URL (streamable HTTP) or, in tests, an in-process server."""
    from mcp import Client

    if server.get("server") is not None:
        return Client(server["server"])
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


def load_tools(servers: list[dict[str, Any]] | None = None) -> list[Tool]:
    """Every tool of every configured MCP server."""
    tools: list[Tool] = []
    for server in configured_servers() if servers is None else servers:
        try:
            tools += [_wrap(server, t) for t in _run(_list, server)]
        except Exception as exc:  # noqa: BLE001 - an unreachable server is skipped, never fatal
            log.warning("MCP server %s skipped: %s", server.get("name"), exc)
    return tools
