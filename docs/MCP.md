# VibeStack and MCP

VibeStack speaks [MCP](https://modelcontextprotocol.io) in both directions:

- **As a server**, AI assistants (Claude Desktop, Cursor, VS Code, or your own agent) can build and edit
  full-stack apps as a tool call.
- **As a client**, the Reflector can call the tools of other MCP servers (documentation search, an internal
  API catalogue, a linter service ...) while it repairs code. See the end of this page.

## VibeStack as an MCP server

- Endpoint: `https://<your-vibestack>/mcp/` (streamable HTTP)
- Authentication: your VibeStack login token, sent as `Authorization: Bearer <token>`. Get it from
  `POST /api/auth/login`. Runs created through MCP belong to you and also show up in the web UI.

## Tools

| Tool | What it does |
|---|---|
| `generate_backend(prompt, stack="fastapi", wait=true)` | Creates a project and runs the whole pipeline (design, rule tests, code, validation, self-healing). Returns a summary: project id, version, entities, actions, bugs found and fixed, stop reason, download path |
| `edit_project(project_id, prompt, wait=true)` | A follow-up change, e.g. "add reviews with a 1-5 rating": builds the next version from the current one |
| `list_projects()` | Your projects with their latest status |
| `keep_fixing(project_id, wait=true)` | Continues self-healing the latest version's own code, e.g. after a failed build |
| `preview_url(run_id)` | Starts the generated app and returns its private live link |
| `download_url(run_id)` | Where to download the project as a zip |
| `run_status(run_id)` | The summary of one run (one version) |
| `list_files(run_id)` | Every file path in the generated project |
| `read_file(run_id, path)` | One file, e.g. `app/services/order.py` |

## Client configuration example

```json
{
  "mcpServers": {
    "vibestack": {
      "type": "http",
      "url": "https://<your-vibestack>/mcp/",
      "headers": { "Authorization": "Bearer <your token>" }
    }
  }
}
```

Then ask the assistant: *"Use VibeStack to build a Django API for a library where members can have at
most 3 open loans."*

## From Python

```python
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=600) as http:
    async with Client(streamable_http_client("http://localhost:8000/mcp/", http_client=http)) as client:
        result = await client.call_tool("generate_backend", {"prompt": "...", "stack": "flask"})
```

`backend/tests/test_mcp.py` does exactly this against a real server.

## VibeStack as an MCP client (tools the agents use by themselves)

When a build fails validation, the Reflector repairs it with tools: `read_file`, `list_files`,
`search_code`, `list_routes`, `write_file` (stage a fix), `run_tests` and `check_frontend`.
Any MCP server can add more, and the agents call them on their own: no Claude Desktop or other AI client is
involved. Bundled servers run inside VibeStack's own container (on Railway too) over stdio. Well-known ones
are turned on by name with `MCP_PRESETS` (default: `builtin`, plus `kubernetes` when a deploy cluster is
set); others are listed in `MCP_SERVERS`, by URL or by command:

```bash
MCP_PRESETS=builtin,docs,kubernetes
MCP_SERVERS='[{"name": "mine", "url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ..."}},
              {"name": "local", "command": "my-mcp-server", "args": ["--stdio"]}]'
```

| Preset | Server | Used by | What it gets | Settings |
|---|---|---|---|---|
| `builtin` | VibeStack's own (`agent/mcp_builtin.py`, stdio, in the container) | Reflector | `python_docs` (the documentation of the exact library versions installed), `package_versions`, `check_manifests` (Kubernetes YAML), `pypi_package` | none |
| `kubernetes` | the open-source [Kubernetes MCP server](https://github.com/containers/kubernetes-mcp-server), read-only, bundled in the Docker image | deploy diagnoser | pods, events, pod logs and resources in the deployed app's namespace, so a failed deploy is explained from the cluster itself | `DEPLOY_KUBECONFIG_B64` (or in-cluster) |
| `docs` | Context7 (`https://mcp.context7.com/mcp`) | Reflector | current documentation of FastAPI, Django, SQLAlchemy, Pydantic … | `CONTEXT7_API_KEY` (optional), `MCP_DOCS_URL` |

Stdio servers get a scrubbed environment (never VibeStack's API keys). Each server's tool list is cached for
5 minutes, and every call has a 30 s timeout.

At the start of each repair, VibeStack asks every server for its tools and offers them to the model as
`<server>__<tool>` (for example `docs__search`). A server that cannot be reached is skipped and logged.
Every call (tool, arguments, first line of the result) is written to the ledger and shown under the
Reflector in the UI. The code is in `backend/agent/mcp_tools.py`; `backend/tests/test_tools.py` runs it
against an in-process MCP server.
