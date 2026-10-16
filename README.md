# VibeStack

**Describe an application in plain English. Eight AI agents design it, build it as a full-stack web app (a React frontend on a FastAPI, Flask or Django backend), test it, find and fix their own bugs, and run it live.**

VibeStack is a LangGraph state machine of eight agents, not a single prompt:

```
prompt ─▶ Architect ─▶ Planner ─▶ Code Generator ─▶ Validator ──ok──▶ Packager ─▶ zip + live app
          requirements, files in    backend +          │ static, import,
          design, rule   build      frontend           │ boot, contract,
          tests (before  order                         │ edge-case and
          any code)                                    │ rule tests
                                                       ▼ fail
                                         Error Classifier ─▶ Reflector ─▶ Validator   (while it makes progress)
                                                              │ reads, searches, stages a fix,
                                                              │ runs the failing tests (tools, MCP)
                                                              ▼ no progress
                                                        Circuit Breaker ─▶ diagnostic report
```

Projects work like a chat. The first message builds version 1. A follow-up such as *"add reviews with a 1–5 rating"* makes the Architect edit the design, the Code Generator rewrite only the changed entities, and the whole app is re-validated as version 2. Any version can be restored.

## The ideas worth explaining

| Idea | Why it matters | Where |
|---|---|---|
| **Healing stops when it stops helping** | There is no fixed "3 attempts". A repair counts as progress if validation reaches a later gate, fewer tests fail, or a new error appears. Without progress the repair escalates: cheap model → strong model → verified templates. It stops when every level fails, when the code returns to an earlier version, or when the budget runs out. | `agent/healing.py` |
| **The Reflector uses tools** | It works like a developer at a terminal: it reads files, searches the code, lists the routes, stages a fix and runs the failing tests *before* it answers. That is at most 6 calls, through the provider's function calling. External MCP servers and plugins can add tools. Every call is in the ledger. | `agent/tools.py`, `agent/mcp_tools.py` |
| **Agents find bugs on their own** | Nothing is planted. Bugs are caught four ways: `ast` static checks (`eval`, shell commands, secrets, SQL strings, debug mode), contract tests, **edge-case tests derived from the design without AI** (unknown foreign key → 409, forbidden state change → 409, another user's row → 404, values out of range → 422 …), and business-rule tests that the Architect writes *before* the code exists. | `agent/bughunt.py`, `agent/codegen.py` |
| **Full-stack contract check** | The generated React app is driven by `config.json` (from the design) and `ui.json` (from the Code Generator). The Validator checks that everything the frontend uses exists in the backend. A mismatch is healed like any other bug. | `agent/frontend.py` |
| **One contract, three frameworks** | FastAPI, Flask and Django share one project layout and one HTTP contract. The tests, the error classifier and the healing loop work unchanged for all three. | `agent/stacks.py`, `agent/templates/` |
| **AI tests are kept honest** | The Reflector may claim that an AI-written rule test contradicts the requirements. A judge (one strong-model call) decides, and every decision is logged. Contract and edge-case tests can never be removed. | `agent/testwriter.py` |
| **Everything is explainable** | Every agent writes to a change ledger: who did what, to which file, why, which tools it used, and the exact diff. | `agent/ledger.py` |

## What it can build

The design (`agent/state.py`) supports:
- typed fields: str, text, int, float, decimal, bool, date, datetime, **json**
- enums with **state machines**, e.g. `draft → running → done | failed`; a forbidden change returns 409
- foreign keys with **delete rules** (`cascade`, `restrict`, `set_null`) and **indexes**
- per-entity access: `any_user`, `owner` (private rows), `admin`
- **custom actions** with business rules, e.g. `POST /orders/{id}/checkout`
- **live updates**: `GET /<table>/stream` (Server-Sent Events) in all three frameworks; the frontend listens to it

Every generated project contains:

| | |
|---|---|
| Frontend | a complete React + Tailwind app (no build step; vendored, works offline): login, a dashboard, a page per entity, forms built from the field rules, state buttons, action buttons and live updates |
| Backend | models, schemas, services (business logic), routers, auth (PBKDF2 + JWT), consistent JSON errors, `/health` and `/ready` |
| Database | Alembic (FastAPI, Flask) or Django migrations, **generated and applied once to prove they work** |
| Tests | contract tests, edge-case tests, rule tests |
| Delivery | a non-root Dockerfile with a health check, docker-compose with Postgres, a `Makefile`, GitHub Actions CI, a README, and `VIBESTACK_LEDGER.md` |

## The platform

- **Projects and versions:** stored with SQLAlchemy Core. SQLite locally and Postgres in production (`PLATFORM_DATABASE_URL`); LangGraph checkpoints use the same database.
- **Live preview:** the generated app runs in a sandbox behind a private `/preview/<key>/` link and is shown in the workspace as a real app, with desktop and phone views.
- **Sandbox:** generated code runs as an unprivileged user, with a scrubbed environment and OS limits on memory, CPU, processes and files.
- **Any model provider:** `LLM_PROVIDER` can be Groq, Gemini, OpenRouter or any OpenAI-compatible API. Rate limits (429) are retried with backoff, and `LLM_MAX_PARALLEL` keeps free tiers happy.
- **MCP:**
  - **Server:** at `/mcp/`, so Claude Desktop, Cursor or VS Code can build and edit projects ([docs/MCP.md](docs/MCP.md)).
  - **Client:** the Reflector can use other MCP servers' tools.
- **Plugins:** one `.py` file adds files, static checks, prompt rules or tools ([docs/PLUGINS.md](docs/PLUGINS.md)).

## Repository layout

```
backend/
  agent/              the harness (plain Python, one idea per file)
    graph.py          LangGraph wiring (8 agents) + CLI
    nodes.py          one function per agent
    state.py          the design (ProjectSpec) + graph state
    healing.py        error taxonomy, fault localisation, when to stop
    tools.py          the Reflector's tools and the Toolbox
    mcp_tools.py      tools from external MCP servers
    frontend.py       generated frontend config + contract check
    stacks.py         FastAPI / Flask / Django: files, commands, dependencies
    codegen.py        renders the templates; derives edge-case tests from the design
    templates/        common/, sqlalchemy/, fastapi/, flask/, django/, frontend/
    sandbox.py        import -> boot -> tests, with OS resource limits
    bughunt.py        static checks, bug tracking
    testwriter.py     rule tests, the judge, safe test removal
    plugins.py        plugin loader
    llm.py            provider client: model tiers, fallback, tool loop, cost
    prompts.py        every prompt in one place
    fake_llm.py       deterministic stand-in model for tests and offline demos
  app/                FastAPI platform: accounts, projects, runs, SSE, previews, MCP server
  plugins/            example plugins
  evals/              15 benchmark prompts + runner with ablations
  tests/              unit, graph, HTTP, MCP, tools and end-to-end tests
frontend/             Next.js UI (static export, served by the backend)
```

## Run it

```bash
cd backend
pip install -e ".[dev]"
export LLM_PROVIDER=groq GROQ_API_KEY=gsk_...    # or FAKE_LLM=1 to run without a key
uvicorn app.main:app --reload --port 8000

cd frontend && npm install && npm run dev        # http://localhost:3000, then create an account
python scripts/check_llm.py                      # checks that the configured models answer
```

The backend serves the UI from `frontend/out/` (a static export). After every `git pull`, run `npm run build` in `frontend/`, or you will see an old build; the header shows the UI version (v3). The server logs a warning when the build is out of date.

CLI only:

```bash
python -m agent.graph "a library where members borrow books, max 3 open loans" --stack django
```

### Docker / Railway

```bash
cp .env.example .env && docker compose up --build    # http://localhost:8000
```

On Railway:
1. Deploy from the `Dockerfile`.
2. Add a **Postgres** service and set `PLATFORM_DATABASE_URL=${{Postgres.DATABASE_URL}}`.
3. Set `LLM_PROVIDER` and its API key, `SECRET_KEY`, `ADMIN_EMAILS`, and optionally `ACCESS_CODE`.
4. Keep a volume at `/data` for the generated zips.

The health check is `/api/health`.

## Tests and evals

```bash
cd backend
pytest -q                                          # ~120 tests: fake model, real sandbox, all three frameworks
python -m evals.run --fake --ablation              # the whole eval harness offline, no tokens
python -m evals.run --hard --stack django          # real model: business-rule prompts
python -m evals.run --hard --ablation              # the ablation table for the paper
```

The ablation compares the full system with three variants:
- **fixed-3:** a fixed 3-repair limit
- **no-tools:** a one-shot Reflector without tools
- **no-edge-tests:** no spec-derived edge-case tests

It reports pass rate, first-try passes, repairs, bugs found and fixed, **escaped bugs** (a held-out run of the edge-case tests on the final code), tool calls, tokens, cost and time. Results go to `evals/results/` as Markdown, JSON and CSV.

## Configuration

See [`.env.example`](.env.example).
