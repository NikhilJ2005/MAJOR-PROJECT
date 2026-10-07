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
2. **Live run (3 min):** pick the **E-commerce** or **Blog + auth** example and set "Demo: plant a bug" to **Wrong HTTP status**, then press **Generate application**.
   - Point at the **"now running" banner** and the pipeline cards: each agent lights up, with the model it used and its time.
   - **Architecture tab:** the Architect turned one paragraph into a data model, endpoints and a file plan.
   - The Validator fails at the API-tests gate. The Error Classifier names it a `contract` error in `app/routers/...`. The Reflector patches it, and the Validator passes. The **activity feed** on the right logs every step.
   - **Self-healing tab:** the planted bug and the fix as a red/green code diff, with no human involved.
   - **▶ Live app tab** (it opens by itself): the generated app is *running*. Click **Sign up**, create a record, create a related record using the dropdown (foreign key), delete one, then **Open API docs ↗** for the live Swagger.
   - Optionally run again with **Broken import** to show a different failure caught at a different gate.
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
