# VibeStack

**Describe an application in plain English. AI agents turn it into a working, tested and bug-hunted backend in FastAPI, Flask or Django.**

VibeStack is not a single prompt. It is a LangGraph state machine of specialised agents:
- An **Analyst** splits the request into testable requirements.
- An **Architect** designs the data model, access rules and custom business actions.
- A **Test Engineer** writes tests *before any code exists*.
- An **API Engineer** writes the code.
- A **sandbox** proves that the code works.

Once the contract passes, a **Bug Hunter** writes tests designed to break the app. Every failure goes through a self-healing loop. The loop keeps going **while it is making progress** and stops on its own when it isn't. No bug is ever planted: everything the agents fix, they found themselves.

```
prompt ─▶ Analyst ─▶ Architect ─▶ Planner ─▶ Test Engineer ─▶ API Engineer ─▶ Validator ──ok──▶ Bug Hunter ─▶ Validator ──ok──▶ Packager ─▶ zip + live app
          (requirements) (entities,   (files)   (rule tests,     (models, schemas,  │ fail            (edge-case      │ fail
                          access,                before code)     services, routers) ▼                  tests)          ▼
                          actions)                                         Classifier ─▶ Reflector ──▶ re-validate (while making progress)
                                                                                         (cheap → strong model → verified templates; may dispute an AI test → Judge)
```

## The ideas worth explaining

| Idea | Why it matters | Where |
|---|---|---|
| **One contract, three frameworks** | All three frameworks share one project layout and one HTTP contract. The spec-derived tests, error classifier and self-healing loop work unchanged for FastAPI, Flask and Django; each framework only adds a small test-client adapter. | `agent/stacks.py`, `agent/templates/` |
| **Tests before code** | The Test Engineer writes business-rule tests from the requirements before the code is written, so the code can't grade its own homework. | `agent/testwriter.py` |
| **Agents find bugs on their own** | Six static checks built on Python's `ast` module run first: `eval`/shell commands, hardcoded secrets, SQL built from strings, debug mode, bare `except`, and `print`. After that, the Bug Hunter's adversarial tests run. Every failure is a real bug and goes through the heal loop. | `agent/bughunt.py` |
| **Healing stops when it stops helping** | There is no fixed "3 attempts". A repair counts as progress if validation reaches a later gate, fewer tests fail, or a new error appears (meaning the old one was fixed). With no progress it escalates: cheap model → strong model → verified templates. It stops when the ladder is used up, when the code returns to an earlier version, or when the budget runs out. A safety limit of 12 attempts is only a seatbelt. | `agent/healing.py` (`should_continue`) |
| **AI tests are kept honest** | The Reflector may claim that an AI-written test contradicts the requirements. A Judge model then decides; a test ruled wrong is removed and logged. The contract tests can never be removed. | `agent/testwriter.py` |
| **Everything is explainable** | Every agent writes to a change ledger: who did what, to which file, why, and the exact diff. It is shipped in every zip. | `agent/ledger.py` |

## What it can build

The spec supports:
- typed fields: str, text, int, float, decimal, bool, date, datetime
- enums (`choices`) and numeric `min`/`max`
- foreign keys
- per-entity access: `any_user`, `owner` (private rows; other users get 404), or `admin` (non-admins get 403)
- **custom actions** with business rules, e.g. `POST /orders/{id}/checkout`: "409 if stock is insufficient". Their logic lives in `app/services/<entity>.py`.

Every generated project contains:

| | |
|---|---|
| Code | models, Pydantic schemas, services (business logic), routers, auth (PBKDF2 + JWT) |
| Database | Alembic (FastAPI, Flask) or Django migrations, **generated and applied once to prove they work**; Postgres via docker-compose |
| Config | environment variables; `SECRET_KEY` is required in production |
| Behaviour | consistent JSON errors, CORS, a request log, `/health` and `/ready` |
| Tests | contract tests, plus rule and edge-case tests when the agents wrote them |
| Delivery | non-root Dockerfile with a health check, `Makefile`, GitHub Actions CI, `.gitignore`, README, and `VIBESTACK_LEDGER.md` (including the bugs found and fixed) |

## The platform

- **Accounts.** Every run belongs to its creator. Other users get 404 for its status, files, downloads and preview. Live previews sit behind an unguessable `/preview/<key>/` link.
- **Sandbox.** Generated code runs as an unprivileged user, with a scrubbed environment (no API keys) and a hard timeout. OS resource limits from the standard `resource` module cap memory, CPU time, file size, open files and processes; a test proves a memory bomb is stopped.
- **Guard rails.** Per-user daily run limit, concurrent-run limit, per-run token budget, optional invite code, and `/api/stats` (success rate, repairs, bugs found and fixed, cost).
- **MCP server** at `/mcp/`. Claude Desktop, Cursor or VS Code can call `generate_backend`. See [docs/MCP.md](docs/MCP.md).
- **Plugins.** Drop a `.py` file in `backend/plugins/` to add files, static checks or prompt rules. See [docs/PLUGINS.md](docs/PLUGINS.md).

## Repository layout

```
backend/
  agent/              the harness (plain Python, one idea per file)
    graph.py          LangGraph wiring + CLI
    nodes.py          one function per agent
    state.py          ProjectSpec (the design) + graph state
    stacks.py         FastAPI / Flask / Django: files, commands, dependencies
    codegen.py        renders the templates for a spec
    templates/        common/ (shared), sqlalchemy/ (FastAPI+Flask), fastapi/, flask/, django/
    sandbox.py        static -> import -> boot -> tests, with OS resource limits
    healing.py        error taxonomy, fault localisation, when to stop
    bughunt.py        static checks, the Bug Hunter, bug tracking
    testwriter.py     Test Engineer, Judge, safe test removal
    analysis.py       requirements and the coverage check
    plugins.py        plugin loader
    llm.py            OpenRouter client: model tiers, fallback, structured output, cost
    prompts.py        every prompt in one place
    fake_llm.py       deterministic stand-in model for tests and offline demos
  app/                FastAPI platform: accounts, runs, SSE, previews, MCP
  plugins/            example plugins
  evals/              15 benchmark prompts (5 hard ones with business rules) + runner
  tests/              unit, graph, HTTP, MCP and browser-free end-to-end tests
frontend/             Next.js UI (static export, served by the backend)
```

## Run it

### Locally

```bash
cd backend
pip install -e ".[dev]"
export OPENROUTER_API_KEY=sk-or-...    # or FAKE_LLM=1 to run without a key
uvicorn app.main:app --reload --port 8000

cd frontend && npm install && npm run dev    # http://localhost:3000, then create an account
```

CLI only:

```bash
cd backend
python -m agent.graph "a library where members borrow books, max 3 open loans" --stack django
python -m agent.graph "library" --template --spec my_spec.json      # no LLM at all
```

### Docker / Railway

```bash
cp .env.example .env && docker compose up --build    # http://localhost:8000
```

On Railway:
1. Deploy from `Dockerfile`.
2. Set `OPENROUTER_API_KEY`, `SECRET_KEY`, `ADMIN_EMAILS` and, optionally, `ACCESS_CODE` (an invite code).
3. Mount a volume at `/data`.

The health check is `/api/health`.

## Tests and evals

```bash
cd backend
pytest -q                                        # ~90 tests: fake model, real sandbox, all three frameworks
python -m evals.run --offline --stack flask      # harness regression, no LLM
python -m evals.run --hard --stack django        # online: business-rule prompts, real LLM
```

Each eval writes a table to `evals/results/` with pass rate, repairs, bugs found and fixed (and how many by the Bug Hunter), stop reason, tokens, cost and time. GitHub Actions runs lint, tests, offline evals on every framework, the frontend build and a Docker build.

## Configuration

See [`.env.example`](.env.example). The most important settings:

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | LLM access |
| `STRONG_MODEL` / `CHEAP_MODEL` / `FALLBACK_MODEL` | Tiered routing: design and code / first repairs and bug hunting / fallback |
| `MAX_HEAL_SAFETY` | Safety limit only (default 12); healing normally stops when it stops making progress |
| `MAX_TOKENS_PER_RUN` | Per-run token budget |
| `MAX_RUNS_PER_DAY`, `MAX_CONCURRENT_RUNS` | Quotas |
| `ACCESS_CODE` | Invite code required to sign up |
| `ADMIN_EMAILS` | Platform admins (platform-wide stats, no daily limit) |
| `SECRET_KEY` | Signs login tokens (generated into `DATA_DIR` if empty) |
| `SANDBOX_MEMORY_MB` | Memory limit for generated code |
| `PLUGINS` | Plugins enabled by default |
| `FAKE_LLM=1` | Deterministic stand-in model for offline demos |

## Roadmap

- Iterative edits ("add reviews to my shop") on an existing project
- A Postgres validation gate (Railway Postgres service)
- Generated projects that ship their own MCP server
- Long-term memory of past fixes
