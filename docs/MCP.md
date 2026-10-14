# Using VibeStack from an AI assistant (MCP)

VibeStack is an [MCP](https://modelcontextprotocol.io) server. Any MCP client (Claude Desktop, Cursor,
VS Code, or your own agent) can generate a validated backend as a tool call.

- Endpoint: `https://<your-vibestack>/mcp/` (streamable HTTP)
- Authentication: your VibeStack login token, sent as `Authorization: Bearer <token>`. Get it from
  `POST /api/auth/login`. Runs created through MCP belong to you and also show up in the web UI.

## Tools

| Tool | What it does |
|---|---|
| `generate_backend(prompt, stack="fastapi", wait=true)` | Runs the whole pipeline (requirements, design, tests, code, bug hunt, self-healing) and returns a summary: entities, actions, bugs found and fixed, stop reason, download path |
| `run_status(run_id)` | The same summary for an earlier run |
| `list_runs()` | Your 20 most recent runs |
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
