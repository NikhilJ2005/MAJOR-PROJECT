# VibeStack v3: status, demo and paper plan (seed-grant submission, 23 Oct)

> **Progress snapshot, 22 Oct:** all code work is merged on `main` (up to PR #8). What is left is
> team work: rotating the secrets after the Railway incident, the provider key, the real-model eval
> numbers, the move to Render and the demo video.
> The code freeze is **today**.

## Done in code (this branch)
- [x] The original **8 agents**:
  - Architect
  - Planner
  - Code Generator
  - Validator
  - Error Classifier
  - Reflector
  - Packager
  - Circuit Breaker
- [x] **Dynamic self-healing**: it stops when repairs stop making progress, not after a fixed count.
- [x] **Complex architectures**: state machines, delete rules, indexes, JSON fields, live updates (SSE) and custom actions, in FastAPI, Flask and Django.
- [x] **Full-stack apps**: the live link is a working React app connected to the generated backend, and a frontend contract check is part of validation.
- [x] **Any model provider**: Gemini (default), Groq, OpenRouter or any OpenAI-compatible API, with rate-limit handling.
- [x] **Projects and versions**: follow-up edits rebuild only what changed, and earlier versions can be restored. Storage is SQLite locally and Postgres in production.
- [x] **Lovable-style UI**: a prompt-first home page, and a workspace with the chat on the left and the live app on the right.
- [x] **Tool-using Reflector**: it reads, searches, stages a fix and runs the failing tests itself. Tools can also come from MCP servers and plugins.
- [x] **Ablation evals** with a held-out escaped-bug check, written as CSV and Markdown.
- [x] **Focused code generation**: the AI writes only the business logic (custom actions); everything else is verified templates. Repairs can only change those files.
- [x] **Rule-test calibration**: the Architect's tests are checked against the verified templates before any code is written.
- [x] **Keep fixing / Rebuild** after a failed version, a **draft preview** within about a minute, and live progress from every agent.

### Added since v3 (PR #7 and PR #8)
**Generation**
- [x] **Enterprise features** as verified templates in FastAPI, Flask and Django:
  - refresh tokens with rotation
  - account lockout
  - an audit log
  - rate limits
  - row locks
- [x] **Design review:** the build pauses after the Architect until the user approves, edits or rewords the design (`DesignReview.tsx`).
- [x] **Agents choose the plugins** a build needs, and say why.
- [x] **Project rules** that every agent follows, and a **guided prompt builder** with six starter apps.
- [x] Generated apps get **security headers** and Prometheus **`/metrics`** in all three frameworks.

**Healing**
- [x] Healing built for complex apps:
  - several broken files are repaired per attempt
  - progress counts per test
  - a bad repair is rolled back
  - every model call has a deadline
- [x] Builds can be **stopped**, have a deadline, and a stopped build ships its best version.
- [x] Faster builds, a **money budget** and a **live code view**.

**Platform**
- [x] **Redis build workers** (`RUN_QUEUE=redis`, `python -m app.worker`): a dead worker's runs continue elsewhere from their last checkpoint.
- [x] Robustness:
  - readiness checks
  - back-pressure
  - idempotent starts
  - shared limits
  - retention
- [x] **Bundled MCP tool servers** inside the container. The Reflector and the deploy diagnoser use them on their own.
- [x] The GitHub sign-in and push feature was **removed on purpose** to keep the scope small.
- [x] About **136 tests** in `backend/tests/`. CI runs them on a real Postgres, with a browser smoke test.

**Deploy**
- [x] **One-click deploy to Kubernetes** of a passing version: its own namespace, Postgres, a migration Job, two replicas and an Ingress, with a smoke test of the public URL (`app/deploy.py`, `DeployPanel.tsx`).
- [x] Kubernetes manifests for VibeStack itself (`deploy/k8s/`), with a worker autoscaler.

## Still to do (team)
Status: ⬜ not started · ❓ unknown, the team should confirm · ✅ done

0. ⬜ **Rotate every secret that was on Railway.** On 21 Oct 2026 Railway reported that environment variables may have been read and decrypted. Deleting them from Railway does not stop a copy from working, so revoke each one where it was issued:
   - the Gemini key (and any OpenRouter or Groq key)
   - `SECRET_KEY` and `ACCESS_CODE`
   - the Railway Postgres: `pg_dump` it only if you need the old projects, then delete the Railway project
1. ❓ **Provider:** create a new Gemini key (aistudio.google.com/apikey) and **enable billing**: Gemini 4 Argon, the default builder, has no free tier. Run `python scripts/check_llm.py --list` to see the model ids, then `python scripts/check_llm.py` (it also checks function calling), then `--full`. Without billing, set `FREE_TIER=1`.
2. ⬜ **Real-model numbers for the slides and paper.** This is the biggest gap: `backend/evals/results/` is not in the repo yet, so no numbers have been committed. Commit the Markdown and CSV results when they are ready. Each command takes about 30–60 minutes on a free tier.
   ```bash
   cd backend
   python -m evals.run --stack fastapi
   python -m evals.run --hard --ablation --stack fastapi
   python -m evals.run --hard --stack django
   python -m evals.run --hard --stack flask
   ```
   If a hard prompt keeps failing, read its ledger (`VIBESTACK_LEDGER.md` in the zip) and tune `prompts.py`.
3. ⬜ **Render + Cloudflare R2** (replaces Railway, see [DEPLOY.md](DEPLOY.md#render-recommended)):
   - In R2, create a bucket and an API token with Object Read & Write.
   - In Render, go to **New → Blueprint** and pick this repo. `render.yaml` creates the web service (Standard, 2 GB, Singapore) and a Postgres.
   - Fill in `GEMINI_API_KEY` (the new one), `ADMIN_EMAILS`, `ACCESS_CODE` and the `S3_*` values. `SECRET_KEY` is generated for you.
   - Build one project per framework, including one follow-up edit, and download a zip to check R2.
   - Cost: about $25/mo for the web service plus about $6/mo for Postgres. R2 is free at this size.
4. ❓ **Budget:** about $0.30–0.80 per app with Gemini 4 Argon (launch price until 30 Nov). A $10–20 top-up covers development and the demo.
5. ⬜ Record a **demo video** as the fallback, then freeze the code on 22 Oct.
6. ⬜ *Optional:* connect a Kubernetes cluster ([DEPLOY.md](DEPLOY.md)) if the demo will show one-click deploy. Otherwise, skip that step in the demo.

## Demo script (about 6 minutes)
1. **Problem (30 s):** AI app builders produce code nobody has checked. Real apps need business rules, permissions and tests, and fixing them should not need a human.
2. **Live build (3 min).** Pick **Agent orchestrator** or **Shop with checkout**, choose Django, then press **Build it**.
   - **Design review:** the build pauses after the Architect. Show the entity diagram and the permission matrix, then approve.
   - **Left panel:** the 8 agents light up one by one.
   - **Design tab:** numbered requirements, each ticked by the entity or action that covers it, plus the state machine and delete rules.
   - **Preview:** the real app. Sign up, create records, move a task from draft to running, and watch the live badge update.
   - **Quality tab:**
     - the bugs the agents found themselves, and who found them
     - the Reflector's tool calls (`read_file`, `write_file`, `run_tests` → PASSED)
     - the fix diff
     - "nothing was planted"
   - **Follow-up:** type *"add reviews with a 1–5 rating"* and wait for version 2. Only the new entity is generated, and the rest is reused.
   - **Deploy (optional):** press deploy and open the public URL once the smoke test passes.
3. **Engineering (1.5 min):**
   - healing that stops on lack of progress (`healing.should_continue`)
   - edge-case tests derived from the design without AI
   - the frontend contract check
   - MCP in both directions
   - one contract for three frameworks
4. **Numbers (1 min):** the ablation table. It shows what each idea buys:
   - escaped bugs without the rule tests, and wrong tests removed by calibration
   - cost and time of the fixed limit versus dynamic stopping
   - repairs with tools versus without

## Likely questions
- **Who checks the AI's tests?** Contract and edge-case tests come from the design without AI and can never be removed. AI-written rule tests can be disputed; a judge decides, and the decision is logged.
- **Why not a fixed number of repairs?** A fixed number either gives up on a long but productive repair or wastes money on a stuck one. We measure progress (a later gate, fewer failures, or a new error), and the ablation shows the difference.
- **Why tools instead of one big prompt?** The Reflector checks its own fix by running the failing test before it answers, the way a developer would. The ledger shows every call.
- **Is generated code safe to run?** It runs as a separate OS user with no secrets and with OS limits on memory, CPU, processes and file size. Static checks block `eval`, shell commands, hardcoded secrets and SQL built from strings.
- **Why does the build pause for design review?** A wrong design produces a wrong app, however well it is tested. Checking the entities, permissions and actions before any code exists is cheap, and saves a whole build.
- **How do I extend it?** A plugin file can add files, checks, prompt rules or tools (`docs/PLUGINS.md`). Any MCP server can lend tools (`docs/MCP.md`).

## Objectives in a standard form (for the report)
1. To design a multi-agent pipeline of eight specialised agents that converts a natural-language application description into a deployable full-stack web application, with a React frontend and a FastAPI, Flask or Django backend.
2. To verify generated applications automatically through static analysis, sandboxed execution, design-derived contract and edge-case tests, business-rule tests written before the code, and a frontend–backend contract check.
3. To repair defects autonomously with a progress-driven self-healing loop that needs no fixed iteration limit, using a tool-augmented repair agent that can inspect code and run tests.
4. To support iterative development, in which follow-up requests produce new, versioned applications that reuse unchanged components.
5. To evaluate the system on a benchmark of 15 applications across three frameworks, with ablations that measure the contribution of dynamic healing, tool use and design-derived tests to success rate, escaped defects, cost and time.
6. To make the system extensible and interoperable through a plugin interface and the Model Context Protocol (MCP), as both server and client.

## Paper outline (journal / conference)
- **Title idea:** *Progress-Driven Self-Healing for Multi-Agent Full-Stack Code Generation with Design-Derived Verification*
- **Contributions:**
  1. A progress-based stopping rule for LLM repair loops.
  2. Design-derived, AI-free edge-case tests as an independent bug oracle.
  3. A tool-augmented repair agent.
  4. A frontend–backend contract check.
  5. A framework-neutral HTTP contract across three Python frameworks.
- **Evaluation:**
  - 15 prompts × 3 frameworks
  - ablations: fixed-3 healing, no tools, no edge tests
  - metrics:
    - pass rate and first-try passes
    - repairs
    - bugs found and fixed
    - escaped bugs (held-out)
    - tokens, cost and time
- **Threats to validity:**
  - The benchmark size.
  - A single provider per run: repeat the evaluation on two providers.
  - The edge-case tests are both a feature and the held-out oracle, so report the held-out numbers only for configurations that did not use them.
