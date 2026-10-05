# First-review plan (review on 8 Oct)

## Goal
Show one loop that works every time and is clearly agentic, rather than many half-built features. That is about 60–65% of the full VibeStack report; everything else goes on the roadmap slide.

## Status (5 Oct)

| Area | State |
|---|---|
| LangGraph graph: spec → approve (interrupt) → plan → generate → review → validate → heal loop → package / circuit breaker | ✅ done, tested |
| OpenRouter client: tiered routing, fallback model, structured output with repair, token and cost accounting, budget | ✅ done, **not yet run against the real API** |
| Sandbox: import, boot and smoke-test gates; scrubbed environment; timeouts; unprivileged user | ✅ done, tested |
| Self-healing: classifier, fault localisation, cheap → strong → template escalation, demo fault injection | ✅ done, tested |
| Change ledger, plus `VIBESTACK_LEDGER.md` in every zip | ✅ done |
| FastAPI: runs, SSE stream with resume, approve/edit/reject, files, zip download, access code, concurrency limit | ✅ done, tested |
| Next.js UI: prompt, live graph, spec approval/editing, ledger, file viewer, review, sandbox log, stats | ✅ done, browser-tested |
| Docker image (UI + API in one service), `railway.json`, compose | ✅ builds and runs |
| Evals: 10 prompts, runner, Markdown/JSON report | ✅ offline 10/10; **online numbers still needed** |

## What's left, by owner

**A: Agent core**
- [ ] Get the OpenRouter key and confirm the model IDs in `.env.example` exist on OpenRouter today. Swap them if not.
- [ ] Run `python -m agent.graph "<prompt>" --fault` with the real key, then tune `agent/prompts.py` until 10 runs in a row succeed.
- [ ] Watch `ENTITY_SYSTEM` compliance (the `### FILE:` block format). If the model drifts, tighten the prompt rather than adding code.

**B: Sandbox and deploy**
- [ ] **Deploy to Railway today** (see README → Railway): variables, a volume at `/data`, a domain, `ACCESS_CODE`.
- [ ] Run 3 generations on the Railway URL and check timings (Railway CPU is slower than a laptop).
- [ ] Stretch, only if everything else is green on 7 Oct: a `DockerSandbox` for the self-hosted track (same `Sandbox` protocol in `agent/sandbox.py`).

**C: Frontend**
- [ ] Polish only: syntax highlighting in the file viewer, a recent-runs list (`GET /api/runs`), a mobile layout check.
- [ ] No new pages or features.

**D: Evals, report, slides**
- [ ] `python -m evals.run` and `python -m evals.run --fault` with the real key. Put the summary line in the slides.
- [ ] Line the report/abstract up with what's built: harness, change ledger, review council, self-healing. Move cut items to "Phase 2".
- [ ] Record a **backup demo video** on 7 Oct (local `docker compose up`, `FAKE_LLM=1` as the last resort).

**Freeze:** no new features after midday on 7 Oct, only fixes.

## Demo script (about 5 minutes)
1. **Problem (30 s):** AI app builders are JS-only, cloud-locked, and don't verify output. Backend generation needs global consistency across models, routes and auth.
2. **Live run (2.5 min):** use the "blog + auth" example with "Inject a bug" ticked.
   - The spec appears. Click **Edit JSON** to show you can change it, then approve.
   - The graph animates. The validator fails at the import gate. The classifier names the file, the reflector patches it, and the validator passes.
   - Open the **Ledger** tab: every agent action with its rationale.
   - Open **Files**: show a router and the spec-derived tests. Download the zip.
3. **Engineering (1.5 min):** the state machine with checkpointing; the human interrupt; cheap → strong → template escalation and the circuit breaker; the sandbox drops privileges; then the eval table (pass rate, average heal iterations, tokens and cost per run).
4. **Roadmap (30 s):** the phase 2 list from the README.

**Fallbacks, in order:** Railway URL → laptop `docker compose up` → recorded video.

## Likely questions and short answers
- **Why LangGraph and not a single prompt?** The workflow has branching (heal loop), pausing (human approval) and durable state (checkpoints). A graph makes that explicit and testable.
- **How do you know the generated code works?** It is executed: imported, booted and tested. The tests come from the spec, so the model can't "grade its own homework".
- **What stops infinite loops and token burn?** The circuit breaker (max 3 attempts), a per-run token budget, a cheap model first, and delta-only context in repairs.
- **Isn't running AI-written code dangerous?** Yes, so it runs as a separate unprivileged user, with no secrets in its environment, its own process group and hard timeouts. On Railway, the container is the outer boundary.
- **What happens if the LLM output is bad?** Structured output is validated with Pydantic and repaired once. Broken code goes through the heal loop. On the last attempt the harness falls back to verified templates, so the user still gets a working backend.
