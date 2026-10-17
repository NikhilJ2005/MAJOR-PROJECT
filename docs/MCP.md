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
| `push_to_github(project_id, repo="")` | Pushes the current version to the user's GitHub (a private repository is created on the first push; the user connects GitHub once in the web app) |
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

## VibeStack as an MCP client (tools for the Reflector)

When a build fails validation, the Reflector repairs it with tools: `read_file`, `list_files`,
`search_code`, `list_routes`, `write_file` (stage a fix), `run_tests` and `check_frontend`.
Any MCP server can add more. Well-known ones are turned on by name with `MCP_PRESETS`; others are
listed in `MCP_SERVERS`:

```bash
MCP_PRESETS=docs,github
MCP_SERVERS='[{"name": "mine", "url": "https://example.com/mcp", "headers": {"Authorization": "Bearer ..."}}]'
```

| Preset | Server | What the Reflector gets | Settings |
|---|---|---|---|
| `docs` | Context7 (`https://mcp.context7.com/mcp`) | current documentation of FastAPI, Django, SQLAlchemy, Pydantic … when a fix needs an API it is unsure about | `CONTEXT7_API_KEY` (optional), `MCP_DOCS_URL` |
| `github` | GitHub's MCP server (`https://api.githubcopilot.com/mcp/`) | repositories, Actions runs and their logs | `GITHUB_MCP_TOKEN` (a read-only token), `MCP_GITHUB_URL` |

At the start of each repair, VibeStack asks every server for its tools and offers them to the model as
`<server>__<tool>` (for example `docs__search`). A server that cannot be reached is skipped and logged.
Every call (tool, arguments, first line of the result) is written to the ledger and shown under the
Reflector in the UI. The code is in `backend/agent/mcp_tools.py`; `backend/tests/test_tools.py` runs it
against an in-process MCP server.
