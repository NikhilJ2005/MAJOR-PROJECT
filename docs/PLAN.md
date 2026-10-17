# VibeStack v3: status, demo and paper plan (seed-grant submission, 23 Oct)

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
- [x] **Projects and versions**: follow-up edits rebuild only what changed, and earlier versions can be restored. Storage is SQLite locally and Postgres on Railway.
- [x] **Lovable-style UI**: a prompt-first home page, and a workspace with the chat on the left and the live app on the right.
- [x] **Tool-using Reflector**: it reads, searches, stages a fix and runs the failing tests itself. Tools can also come from MCP servers and plugins.
- [x] **Ablation evals** with a held-out escaped-bug check, written as CSV and Markdown.
- [x] **Focused code generation**: the AI writes only the business logic (custom actions); everything else is verified templates. Repairs can only change those files.
- [x] **Rule-test calibration**: the Architect's tests are checked against the verified templates before any code is written.
- [x] **Keep fixing / Rebuild** after a failed version, a **draft preview** within about a minute, and live progress from every agent.

## Still to do (team)
1. **Provider:** create a Gemini key (aistudio.google.com/apikey) and **enable billing**: Gemini 4 Argon, the default builder, has no free tier. Run `python scripts/check_llm.py --list` to see the model ids, then `python scripts/check_llm.py` (it also checks function calling), then `--full`. Without billing, set `FREE_TIER=1`.
2. **Real-model numbers for the slides and paper.** Each command takes about 30–60 minutes on a free tier.
   ```bash
   cd backend
   python -m evals.run --stack fastapi
   python -m evals.run --hard --ablation --stack fastapi
   python -m evals.run --hard --stack django
   python -m evals.run --hard --stack flask
   ```
   If a hard prompt keeps failing, read its ledger (`VIBESTACK_LEDGER.md` in the zip) and tune `prompts.py`.
3. **Railway:**
   - Add a Postgres service and set `PLATFORM_DATABASE_URL=${{Postgres.DATABASE_URL}}`.
   - Set `LLM_PROVIDER=gemini`, `GEMINI_API_KEY`, `SECRET_KEY`, `ADMIN_EMAILS` and `ACCESS_CODE`.
   - Delete `OPENROUTER_API_KEY`, `STRONG_MODEL`, `CHEAP_MODEL` and `FALLBACK_MODEL` if they are set.
   - Keep the `/data` volume.
   - Build one project per framework, including one follow-up edit.
4. **Budget:** about $0.30–0.80 per app with Gemini 4 Argon (launch price until 30 Nov). A $10–20 top-up covers development and the demo.
5. Record a **demo video** as the fallback, then freeze the code on 22 Oct.

## Demo script (about 6 minutes)
1. **Problem (30 s):** AI app builders produce code nobody has checked. Real apps need business rules, permissions and tests, and fixing them should not need a human.
2. **Live build (3 min).** Pick **Agent orchestrator** or **Shop with checkout**, choose Django, then press **Build it**.
   - **Left panel:** the 8 agents light up one by one.
   - **Design tab:** numbered requirements, each ticked by the entity or action that covers it, plus the state machine and delete rules.
   - **Preview:** the real app. Sign up, create records, move a task from draft to running, and watch the live badge update.
   - **Quality tab:**
     - the bugs the agents found themselves, and who found them
     - the Reflector's tool calls (`read_file`, `write_file`, `run_tests` → PASSED)
     - the fix diff
     - "nothing was planted"
   - **Follow-up:** type *"add reviews with a 1–5 rating"* and wait for version 2. Only the new entity is generated, and the rest is reused.
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
