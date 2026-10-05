# VibeStack

**An agentic harness that turns a natural-language description into a validated, self-healed FastAPI backend.**

VibeStack is not a single prompt. It is a LangGraph state machine: specialised agents design the data model, a human approves it, the code is generated and reviewed, and a sandbox runs import, boot and contract tests. Failures go through a classify → reflect → re-validate loop with a circuit breaker. Every action is written to a change ledger, so you can see what each agent did and why.

```
            ┌──────────────┐   interrupt()   ┌──────────────┐
 prompt ──▶ │ Spec Architect├───────────────▶│Human Approval│── reject ──▶ END
            └──────────────┘  ProjectSpec    └──────┬───────┘
                                                    ▼
              Planner ──▶ API Engineer ──▶ Review Council ──▶ Sandbox Validator ──ok──▶ Packager ──▶ zip
                                                                 ▲        │fail
                                                                 │        ▼
                                                            Reflector ◀── Error Classifier
                                                         (cheap → strong → templates)
                                                                 │ attempts exhausted
                                                                 ▼
                                                          Circuit Breaker ──▶ diagnostic report
```

## What it does

| Stage | Agent / node | How |
|---|---|---|
| Understand | **Spec Architect** | The LLM returns a Pydantic `ProjectSpec` (entities, typed fields, FKs, auth). It is validated, normalised and FK-topologically sorted. A repair round-trip runs on invalid JSON. |
| Control | **Human approval** | LangGraph `interrupt()`. The UI shows the data model; you approve it, edit the JSON, or reject it. |
| Plan | **Planner** | Deterministic file plan in dependency order. Verified Jinja2 templates render a known-good baseline. |
| Build | **API Engineer** | One LLM call per entity, run in parallel. It rewrites models, schemas and routers against a fixed HTTP contract. |
| Review | **Review Council** | Static security rules plus an LLM reviewer (unauthenticated writes, injection, unbounded queries, secret exposure). Advisory only. |
| Verify | **Sandbox Validator** | Gate 1: `import app.main`. Gate 2: uvicorn boot plus `GET /health`. Gate 3: pytest smoke tests derived from the spec, not from the code. |
| Heal | **Classifier → Reflector** | A regex taxonomy (syntax, import, dependency, orm, validation, runtime, contract, timeout) plus traceback fault localisation. The repair gets only the failing file and its siblings (delta context), and escalates cheap model → strong model → verified templates. |
| Stop | **Circuit breaker** | After `MAX_HEAL_ITERATIONS`, stops and writes a diagnostic report. |
| Deliver | **Packager** | Zip containing `app/`, tests, `pyproject.toml`, `Dockerfile`, `docker-compose.yml` (Postgres) and `VIBESTACK_LEDGER.md`. |

Engineering details worth asking about:
- **State and durability.** A typed `AgentState` with reducers (files are merged, ledger entries appended, token usage summed). A SQLite checkpointer persists every step, so a pending approval survives a server restart.
- **Cost control.** Tiered model routing, a fallback model, per-run token budget, and per-run tokens and estimated cost shown in the UI.
- **Sandbox isolation.**
  - Each gate is a subprocess with a scrubbed environment, its own process group and a hard timeout.
  - In the Docker image, generated code runs as a separate unprivileged `sandbox` user, so it cannot read the server's API key from `/proc`. A test covers this.
- **Evals.** `python -m evals.run` runs 10 benchmark prompts and reports pass rate, first-try passes, heal iterations, tokens, cost and latency.

## Repository layout

```
backend/
  agent/            the harness
    graph.py        LangGraph wiring + CLI
    nodes.py        all agents / nodes
    state.py        AgentState + ProjectSpec IR
    llm.py          OpenRouter client: routing, fallback, structured output, usage
    codegen.py      file plan + Jinja2 rendering + demo fault injection
    sandbox.py      3-gate validator
    healing.py      error taxonomy + fault localisation
    prompts.py      all prompts in one place
    fake_llm.py     deterministic model for tests / UI dev (FAKE_LLM=1)
    templates/      verified templates for generated projects
  app/              FastAPI API: runs, SSE events, approval, files, download
  evals/            benchmark prompts + runner
  tests/            unit, graph and HTTP tests
frontend/           Next.js UI (static export, served by the backend)
Dockerfile          single image: API + UI (what Railway runs)
docker-compose.yml  self-hosted track (same image)
docs/PLAN.md        scope, team split, schedule, demo script
```

## Run it

### Locally (development)

```bash
# backend
cd backend
pip install -e .                       # or: uv pip install -e .
export OPENROUTER_API_KEY=sk-or-...    # or FAKE_LLM=1 to work without a key
uvicorn app.main:app --reload --port 8000

# frontend (separate terminal)
cd frontend && npm install && npm run dev    # http://localhost:3000
```

CLI only, no UI:

```bash
cd backend
python -m agent.graph "a todo API where users log in and manage tasks" --fault
python -m agent.graph "library" --template --spec my_spec.json      # no LLM at all
```

### Self-hosted (one command)

```bash
cp .env.example .env      # set OPENROUTER_API_KEY (or FAKE_LLM=1)
docker compose up --build # http://localhost:8000
```

### Railway (hosted)

1. Create a Railway project from this GitHub repo. It builds from `Dockerfile` (see `railway.json`).
2. Variables: `OPENROUTER_API_KEY` and `ACCESS_CODE` (anyone with the link spends your credits otherwise). Optionally set `STRONG_MODEL`, `CHEAP_MODEL` and `MAX_CONCURRENT_RUNS`.
3. Add a **Volume** mounted at `/data`, so checkpoints and generated zips survive redeploys.
4. Generate a domain. The health check is `/api/health`.

Railway containers cannot run Docker. That's why the validator uses the subprocess sandbox, and the Railway container is the isolation boundary. The same image runs locally via compose.

## Tests and evals

```bash
cd backend
pytest -q                          # unit + graph + HTTP end-to-end (fake LLM, real sandbox)
python -m evals.run --offline      # harness regression on reference specs, no LLM
python -m evals.run --fault        # online: real LLM, a bug injected into every project
python -m evals.run                # online: the headline numbers for the slides
```

Results are written to `backend/evals/results/` as Markdown and JSON.

## Configuration

See [`.env.example`](.env.example). The important variables:

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | LLM access (OpenRouter) |
| `STRONG_MODEL` / `CHEAP_MODEL` / `FALLBACK_MODEL` | Tiered routing |
| `CODEGEN_MODE` | `llm` (model writes entity code) or `template` (deterministic) |
| `MAX_HEAL_ITERATIONS` | Circuit breaker limit (default 3) |
| `MAX_TOKENS_PER_RUN` | Per-run token budget |
| `ACCESS_CODE` | Required to start runs on a public deployment |
| `FAKE_LLM=1` | Deterministic stand-in model for UI work and offline demos |

## Roadmap (phase 2)

These are deliberately out of scope for the first review:
- ChromaDB long-term memory
- MCP tool servers
- Trivy and Checkov scanning
- Alembic migrations
- Postgres-backed validation
- Docker-per-run sandbox for the self-hosted track
- Iterative "add a feature to an existing project" edits
- A multi-agent council debate
- Java / Spring Boot output
