# Review on 8 Oct: demo plan

## Scope for the review
One story: **prompt → architecture → working app is built → self-healing fixes a bug**.
Human approval and the review council were cut; they're on the roadmap slide.

## Tonight's checklist
1. **OpenRouter:** top up **$10**, then create a **new** key. Revoke any key that was ever pasted into a chat.
2. **Laptop check:**
   ```bash
   cd backend && pip install -e .
   OPENROUTER_API_KEY=sk-or-... python scripts/check_llm.py --full
   ```
   - All three models should print `OK`, followed by one full run ending in `status: succeeded`.
   - If a model fails, look up the right id on https://openrouter.ai/models and set `STRONG_MODEL`, `CHEAP_MODEL` or `FALLBACK_MODEL`.
3. **Rehearse in the UI 3–5 times** with the "blog + auth" and "library" examples (see README → Run it).
   - If code generation often needs 2+ heals, keep Sonnet.
   - If credit is tight, switch to `STRONG_MODEL=qwen/qwen3-coder`.
4. **Railway:**
   - Variables: `OPENROUTER_API_KEY`, the three model variables, `ACCESS_CODE`, `MAX_CONCURRENT_RUNS=1`.
   - Volume at `/data`, then generate a domain.
   - Do one full run there.
5. **Local backup:** `cp .env.example .env` (fill in the key), then `docker compose up --build` → http://localhost:8000. Test it once.
6. **Record a backup video** of one full run. Freeze the code.

## Demo script (about 5 minutes)
1. **Problem (30 s):** AI app builders are JS-only, cloud-locked, and don't check that what they generate actually runs.
2. **Live run (2.5 min):** use the "blog + auth" example with "Inject a bug" ticked, then press **Generate application**.
   - **Architecture tab:** the Architect turned the sentence into a data model, endpoints and a file plan.
   - The graph animates. The validator fails, the classifier names the file, the reflector patches it, and the validator passes.
   - **Self-healing tab:** the bug, the diagnosis and the fix, with no human involved.
   - **Tests tab:** imports ✓, server boots ✓, API tests ✓, with each test listed as PASSED.
   - **Code tab:** a generated router. Then **Download .zip**: a complete project with Dockerfile and docker-compose.
3. **Engineering (1.5 min):**
   - The LangGraph state machine with checkpointing.
   - Tiered models: Sonnet builds; Qwen Coder repairs first, then escalation and the circuit breaker.
   - The tests come from the architecture, not the generated code.
   - Generated code runs as an unprivileged user.
4. **Roadmap (30 s):** human approval, a review council, long-term memory, MCP, security scanning, Java.

**Fallbacks, in order:** Railway URL → `docker compose up` on the laptop → recorded video.

## Likely questions and short answers
- **Why LangGraph and not a single prompt?** The workflow loops (heal), branches (pass/fail/give up) and keeps state across steps. A graph makes that explicit, checkpointed and testable.
- **How do you know the generated app works?** It is executed: imported, booted and tested. The tests come from the architecture, so the model can't grade its own homework.
- **What stops infinite loops and token burn?** Max 3 heal attempts, a per-run token budget, a cheap model first, and repairs get only the failing file plus its siblings.
- **What if the AI writes bad code?** The heal loop fixes it. On the final attempt the harness restores verified templates, so the user still gets a working backend.
- **Cost?** About $0.15–0.20 per app with Sonnet, about $0.015 with Qwen Coder only.
