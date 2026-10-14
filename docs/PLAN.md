# VibeStack v2: sprint plan and demo (seed-grant submission, 23 Oct)

## Done in code (this branch)
- [x] No planted bugs. Healing stops when it stops making progress (`healing.should_continue`).
- [x] FastAPI, Flask and Django generation from one contract.
- [x] Complex requests: Requirements Analyst, access rules (owner/admin), enums, min/max, custom actions with business rules, Test Engineer (tests before code).
- [x] Bug hunting: `ast` static checks plus the Bug Hunter agent; Judge for disputed AI tests.
- [x] Production basics:
  - Platform: accounts, run ownership, private previews, quotas, OS sandbox limits, stats, CI.
  - Generated apps: migrations, env config, JSON errors, `/ready`, Makefile, CI, non-root Docker image.
- [x] MCP server (`/mcp/`) and plugins (`backend/plugins/`).

## Still to do (team)
1. **Real-model evals:** run each command and put the tables in the slides (cost: a few dollars).
   ```bash
   cd backend
   python -m evals.run --stack fastapi
   python -m evals.run --hard --stack fastapi
   python -m evals.run --hard --stack django
   ```
   If a hard prompt fails often, read its ledger (`VIBESTACK_LEDGER.md` in the zip) and tune `prompts.py`.
2. **Railway:**
   - Set `OPENROUTER_API_KEY`, `SECRET_KEY`, `ADMIN_EMAILS` (your emails) and `ACCESS_CODE` (an invite code for the judges).
   - Mount a volume at `/data`.
   - Do one full run per framework.
3. **MCP demo:** connect Claude Desktop (or the MCP Inspector) to `<railway-url>/mcp/` with your token (see `docs/MCP.md`).
4. **Demo video** as the fallback, then freeze the code on 22 Oct.

## Demo script (about 6 minutes)
1. **Problem (30 s):** AI app builders produce code that nobody has checked, mostly JavaScript. Backends need business rules, permissions and tests.
2. **Live run (3 min):** log in, choose **Django**, the **Shop checkout (rules)** example and the `kubernetes` and `postman` plugins, then press **Generate**.
   - **Architecture tab:** the Analyst's numbered requirements, each ticked by the entity or action that covers it; private orders, admin-only products, and the `checkout` action with its rules.
   - The pipeline: the Test Engineer writes tests *before* the code; the Validator runs static checks → import → boot → tests.
   - **Bug hunt:** the Bug Hunter writes tests to break the app. Show **Bugs & healing**: who found each bug, the fix diff, and "nothing was planted".
   - **Live app:** log in as **customer**, create an order, run `checkout`; log in as **admin**, add a product; switch back to show that private rows disappear and that admin-only writes return 403.
   - **Download .zip:** migrations, Makefile, CI, k8s manifests, Postman collection, the ledger.
3. **Engineering (1.5 min):**
   - One contract for three frameworks.
   - Healing that stops on lack of progress (show `should_continue`).
   - The Judge.
   - OS-level sandbox limits.
   - MCP: the same pipeline as a tool for AI assistants.
4. **Numbers (30 s):** the eval tables: pass rate, bugs found by the agents, repairs, cost per app.

## Likely questions
- **Who checks the AI's tests?** The contract tests come from the design and can never be removed. AI-written tests can be disputed; a Judge decides, and every decision is in the ledger.
- **Why not a fixed number of repairs?** A fixed number either gives up too early on a long but productive repair or wastes money on a stuck one. We measure progress: later gate, fewer failures, or a new error.
- **Is generated code safe to run?** It runs as a separate OS user, with no secrets in its environment and OS limits on memory, CPU, processes and file size. Static checks also block `eval`, shell commands, hardcoded secrets and SQL built from strings.
- **How do I extend it?** Add a plugin file (`docs/PLUGINS.md`) or a framework (`agent/stacks.py` plus a template folder).

## Objectives in a standard form (for the report)
1. To design a multi-agent pipeline that converts a natural-language application description into a deployable REST backend in FastAPI, Flask or Django.
2. To verify generated code automatically through static analysis, execution in an isolated sandbox, and tests derived from the design and written before the code.
3. To detect defects autonomously, through adversarial test generation, and to repair them with a progress-driven self-healing loop that needs no fixed iteration limit.
4. To evaluate the system on a benchmark of 15 applications (5 with business rules), measuring success rate, defects found and repaired, cost and time per application.
5. To make the system extensible and interoperable through a plugin interface and the Model Context Protocol (MCP).
