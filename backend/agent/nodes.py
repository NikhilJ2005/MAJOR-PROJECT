"""LangGraph nodes: one function per agent (8 agents). Each takes the state and returns what it changed.

    parse_spec      Architect          prompt -> requirements + design (+ rule tests, before any code)
    plan            Planner            file plan + verified templates for the chosen framework
    generate        Code Generator     backend (models, schemas, services, routers) + frontend
    validate        Validator          static checks -> import -> boot -> contract, edge-case and rule tests
    classify        Error Classifier   which kind of failure, in which file
    reflect         Reflector          repair with tools, escalating only when there is no progress
    package         Packager           migrations, README, zip
    failure_report  Circuit Breaker    diagnostic report when healing stops making progress

Every node appends to the change ledger, which makes a run explainable after the fact.
"""

from __future__ import annotations

import ast
import contextvars
import json
import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langgraph.types import interrupt

from . import plugins, prompts
from .analysis import check_coverage, requirements_text
from .bughunt import failing_tests, static_check, update_bugs
from .codegen import plan_files, render_project
from .config import settings
from .frontend import UI, UIConfig, check_frontend, default_ui
from .healing import (
    best_index,
    classify_error,
    editable,
    failing_targets,
    guess_service,
    history_entry,
    locate_failing_file,
    regressed,
    related_files,
    repair_rung,
    repair_target,
    scaled_limits,
    should_continue,
)
from .ledger import (
    LiveText,
    diff,
    entry,
    ledger_markdown,
    live_writer,
    model_names,
    progress,
    write_artifact,
)
from .llm import (
    LLMError,
    Usage,
    ask,
    budget_left,
    check_budget,
    get_llm,
    parse_file_blocks,
    structured,
)
from .mcp_tools import load_tools as load_mcp_tools
from .sandbox import GateResult, get_sandbox
from .stacks import get_stack
from .state import AgentState, EntitySpec, ProjectSpec
from .testwriter import (
    AI_TEST_FILES,
    EDGE_TESTS,
    RULE_TESTS,
    calibrate,
    get_test_source,
    judge,
    needs_rule_tests,
    parse_dispute,
    remove_test,
    touches_action,
    write_rule_tests,
)
from .tools import Toolbox

log = logging.getLogger(__name__)
SANDBOX_RETRY_WAIT_S = 2


def _spec(state: AgentState) -> ProjectSpec:
    return ProjectSpec.model_validate(state["spec"])


def _opts(state: AgentState) -> dict[str, Any]:
    return dict(state.get("options") or {})


def _plugins(state: AgentState) -> list[tuple[str, Any]]:
    """The plugins the Planner chose for this build (or, before planning, the ones the user asked for)."""
    if state.get("plugin_choices") is not None:
        return plugins.chosen([c["name"] for c in state["plugin_choices"]])
    return plugins.chosen(_opts(state).get("plugins"))


def _rules(state: AgentState) -> str:
    """The user's project rules, as a prompt section ("" when there are none)."""
    rules = (_opts(state).get("instructions") or "").strip()
    return prompts.PROJECT_RULES.format(rules=rules) if rules else ""


def _count_tests(source: str) -> int:
    return len(re.findall(r"^def test_", source, re.MULTILINE))


def _require_llm() -> Any:
    llm = get_llm()
    if llm is None:
        raise LLMError("no LLM API key is set (GROQ_API_KEY, GEMINI_API_KEY or OPENROUTER_API_KEY) and no spec was provided")
    return llm


# --------------------------------------------------------------------------- Architect


def _with_stack(spec: ProjectSpec, stack: str | None) -> ProjectSpec:
    """The design with the framework the user picked."""
    raw = spec.model_dump()
    raw["stack"] = stack or raw.get("stack") or "fastapi"
    return ProjectSpec.model_validate(raw)


def _design(llm: Any, state: AgentState) -> tuple[ProjectSpec, list[str], Usage]:
    """Call 1: requirements + design in one reply, with one retry if a requirement is not covered.

    For a follow-up edit the Architect gets the current design plus the change request.
    """
    base = ProjectSpec.model_validate(state["base_spec"]) if state.get("base_spec") else None
    stack = base.stack if base else _opts(state).get("stack")
    if base:
        user = prompts.EDIT_USER.format(spec=base.model_dump_json(indent=1, exclude={"stack"}), prompt=state["prompt"])
    else:
        user = prompts.SPEC_USER.format(prompt=state["prompt"])

    live = LiveText(live_writer(), "architect", "Design (JSON)")

    def design(text: str, usage: Usage | None = None) -> tuple[ProjectSpec, Usage]:
        system = prompts.SPEC_SYSTEM + prompts.PLUGIN_CATALOG.format(catalog=plugins.catalog() or "(none)") + _rules(state)
        spec, used = structured(llm, "strong", system, text, ProjectSpec, effort="high", stream=live)
        live.done()
        if usage:
            used.add(usage)
        raw = spec.model_dump()
        if base:
            raw["project_name"] = base.project_name  # an edit never renames the project
        return _with_stack(ProjectSpec.model_validate(raw), stack), used

    spec, usage = design(user)
    missing = check_coverage(spec)
    if missing:
        spec, usage = design(user + "\n\n" + prompts.SPEC_COVERAGE_RETRY.format(missing=", ".join(missing)), usage)
        missing = check_coverage(spec)
    return spec, missing, usage


def design_changes(old: ProjectSpec, new: ProjectSpec) -> dict[str, list[str]]:
    """Which entities a follow-up edit added, changed or removed (actions count as part of their entity)."""
    def shape(spec: ProjectSpec) -> dict[str, str]:
        return {e.name: json.dumps([e.model_dump(exclude={"requirement_ids"}), [a.model_dump() for a in spec.actions_for(e)],
                                    spec.auth]) for e in spec.entities}

    before, after = shape(old), shape(new)
    return {
        "added": [n for n in after if n not in before],
        "changed": [n for n in after if n in before and before[n] != after[n]],
        "removed": [n for n in before if n not in after],
    }


def _rule_tests(llm: Any, spec: ProjectSpec, ledger: list[dict[str, Any]], usage: Usage,
                calibrated: bool = True, writer: Any = None) -> dict[str, str]:
    """The Architect's second job: business-rule tests from the requirements and the design only (they never
    see code), then calibrated against the verified templates. Runs alongside the Code Generator."""
    if not needs_rule_tests(spec):
        return {}
    live = LiveText(writer, "architect", RULE_TESTS)
    try:
        source, more = write_rule_tests(llm, spec, stream=live)
        live.done()
    except LLMError as exc:
        ledger.append(entry("architect", "no rule tests written", str(exc)))
        return {}
    usage.add(more)
    if source is None:
        ledger.append(entry("architect", "rule-test reply was not usable", "relying on contract + edge-case tests"))
        return {}
    ledger.append(entry("architect", f"wrote {_count_tests(source)} business-rule tests",
                        "from the requirements only, before any code exists", files=[RULE_TESTS]))
    if calibrated:
        source, removed = calibrate(source, spec, render_project(spec))
        if removed:
            ledger.append(entry("architect", f"removed {len(removed)} rule test(s) that contradict the verified contract",
                                "calibration: the verified template code fails them, so the tests are wrong: "
                                + ", ".join(removed)))
    return {RULE_TESTS: source} if source else {}


def _redesign(state: AgentState) -> dict[str, Any]:
    llm = _require_llm()
    progress("parse_spec", "Changing the design as you asked…")
    spec, missing, usage = _design(llm, {**state, "base_spec": state["spec"], "prompt": state["design_feedback"]})
    coverage = "all requirements covered" if not missing else f"not covered: {', '.join(missing)}"
    ledger = [entry("architect", f"changed the design: {state['design_feedback'][:120]}", coverage,
                    model=model_names(usage), tokens=usage.total_tokens)]
    out = {"spec": spec.model_dump(), "design_feedback": None, "status": "spec_ready", "usage": usage.as_state(),
           "ledger": ledger, "rule_tests_wanted": True}
    if state.get("base_spec") and not state.get("base_failed"):  # an edit of an earlier version: what changed since it
        out["changes"] = design_changes(ProjectSpec.model_validate(state["base_spec"]), spec)
    return out


def parse_spec(state: AgentState) -> dict[str, Any]:
    """Architect: prompt -> requirements + a validated design. (Its rule tests are written in parallel with
    the code, from the design alone: see generate.)"""
    stack = _opts(state).get("stack")
    if state.get("design_feedback"):  # the user asked for a change on the design screen: edit the design
        return _redesign(state)
    if state.get("spec"):
        spec = _with_stack(ProjectSpec.model_validate(state["spec"]), stack)
        return {
            "spec": spec.model_dump(),
            "status": "spec_ready",
            "ledger": [entry("architect", f"used provided spec ({spec.stack})", "spec supplied with the request")],
        }
    llm = _require_llm()
    progress("parse_spec", "Designing the data model, rules and screens…")
    spec, missing, usage = _design(llm, state)
    progress("parse_spec", f"Designed {len(spec.entities)} entities")
    changes = design_changes(ProjectSpec.model_validate(state["base_spec"]), spec) if state.get("base_spec") else None
    kinds = Counter(r.kind for r in spec.requirements)
    names = ", ".join(e.name for e in spec.entities)
    action = f"listed {len(spec.requirements)} requirements ({', '.join(f'{n} {k}' for k, n in kinds.most_common())}); "
    action += f"designed {len(spec.entities)} entities ({names})"
    action += f", {len(spec.actions)} custom actions" if spec.actions else ""
    action += " + JWT auth" if spec.auth else ""
    coverage = "all requirements covered" if not missing else f"not covered: {', '.join(missing)}"
    if changes is not None:
        summary = "; ".join(f"{kind}: {', '.join(names)}" for kind, names in changes.items() if names) or "no entity changed"
        action = f"updated the design ({summary})"
        if state.get("base_failed"):  # never reuse code from a version that did not work
            action += "; the previous version failed, so every entity is rebuilt"
            changes = None
    ledger = [entry("architect", action, coverage, model=model_names(usage), tokens=usage.total_tokens)]
    # The rule tests come next, written at the same time as the code (see generate).
    out: dict[str, Any] = {"spec": spec.model_dump(), "status": "spec_ready", "usage": usage.as_state(), "ledger": ledger,
                           "rule_tests_wanted": True}
    if changes is not None:
        out["changes"] = changes
    return out


# --------------------------------------------------------------------------- plan + generate


def plan(state: AgentState) -> dict[str, Any]:
    reviewed: list[dict[str, Any]] = []
    if _opts(state).get("review_design"):
        # Pause here until the user approves the design (POST /api/runs/{id}/approve). Nothing runs before
        # this line, so the node can safely start over when the run continues.
        decision = interrupt({"kind": "design_review"}) or {}
        if decision.get("instruction"):
            return {"design_feedback": decision["instruction"], "status": "spec_ready",
                    "ledger": [entry("planner", "you asked for a design change", decision["instruction"])]}
        if decision.get("spec"):
            edited = _with_stack(ProjectSpec.model_validate(decision["spec"]), _spec(state).stack)
            state = {**state, "spec": edited.model_dump()}
            reviewed.append(entry("planner", "you edited and approved the design", "changes made on the design screen"))
        else:
            reviewed.append(entry("planner", "you approved the design", "no code is written before your approval"))
    spec = _spec(state)
    choices = plugins.resolve(_opts(state), [c.model_dump() for c in spec.plugins], spec, state.get("prompt", ""))
    state = {**state, "plugin_choices": choices}
    file_plan = plan_files(spec)
    template_files = render_project(spec)
    if _opts(state).get("edge_tests") is False:  # ablation study: run without the spec-derived edge-case tests
        template_files.pop(EDGE_TESTS, None)
        file_plan = [f for f in file_plan if f["path"] != EDGE_TESTS]
    who = {"user": "you", "architect": "the Architect", "planner": "the Planner", "default": "the server settings"}
    ledger = reviewed + [entry("planner", f"turned on the {c['name']} plugin (chosen by {who[c['by']]})", c["reason"])
                         for c in choices]
    ledger.append(entry(
        "planner",
        f"planned {len(file_plan)} {get_stack(spec.stack).label} files in dependency order",
        "models -> schemas -> services -> routers -> app wiring -> tests -> packaging",
    ))
    for name, added in plugins.extra_files(_plugins(state), spec).items():
        added = {p: c for p, c in added.items() if p not in template_files}  # plugins add, never overwrite
        template_files.update(added)
        file_plan += [{"path": p, "owner": f"plugin:{name}", "purpose": f"added by the {name} plugin"} for p in sorted(added)]
        if added:
            ledger.append(entry(f"plugin:{name}", f"added {len(added)} file(s)", "extra_files hook", files=sorted(added)))
    return {
        "file_plan": file_plan,
        "template_files": template_files,
        "plugin_choices": choices,
        "spec": spec.model_dump(),
        "design_feedback": None,
        "status": "planned",
        "ledger": ledger,
    }


def _submit(pool: ThreadPoolExecutor, fn: Any, *args: Any) -> Any:
    """Run fn in the pool with a copy of this node's context, so live progress and streaming work there too."""
    return pool.submit(contextvars.copy_context().run, fn, *args)


def generate(state: AgentState) -> dict[str, Any]:
    """Code Generator: business logic + the app's look, while the Architect writes its rule tests.

    Everything here depends only on the design, so the jobs run at the same time: one per entity with
    custom actions, one for the look, one for the rule tests (which never see the code).
    """
    spec = _spec(state)
    files = dict(state["template_files"])
    ledger: list[dict[str, Any]] = []
    total = Usage()
    mode = _opts(state).get("codegen_mode") or settings.codegen_mode
    model = get_llm()
    llm = model if mode == "llm" else None
    writer = live_writer()

    # Only the business logic is written by the model: every model, schema and router comes from the
    # verified templates. Entities without custom actions need no model call at all.
    with_actions = [e for e in spec.entities if any(a.entity == e.name for a in spec.actions)]

    # Follow-up edit: entities the Architect did not touch keep their business logic from the version before.
    changes, base_files = state.get("changes"), state.get("base_files") or {}
    todo = with_actions
    if changes is not None:
        touched = set(changes["added"]) | set(changes["changed"])
        kept = [e for e in spec.entities if e.name not in touched]
        for e in kept:
            path = f"app/services/{e.module}.py"
            if path in base_files:
                files[path] = base_files[path]
        todo = [e for e in with_actions if e.name in touched]
        if kept:
            ledger.append(entry("code_generator", f"kept {', '.join(e.name for e in kept)} from version before",
                                "unchanged by the edit, so their code (and its fixes) is reused"))

    jobs: dict[str, Any] = {}
    # Not a `with` block: leaving it would wait for a job that is stuck. Every model call has its own
    # deadline, and JOB_TIMEOUT is the backstop for a whole job (call, syntax fix, calibration run).
    pool = ThreadPoolExecutor(max_workers=max(2, settings.llm_max_parallel))
    try:
        if model is not None and state.get("rule_tests_wanted"):
            test_ledger: list[dict[str, Any]] = []
            test_usage = Usage()
            jobs["rules"] = _submit(pool, _rule_tests, model, spec, test_ledger, test_usage,
                                    _opts(state).get("calibrate", True), writer)
        if llm is not None:
            for e in todo:
                jobs[e.name] = _submit(pool, _generate_service, llm, state, spec, e, files, writer)
            same_entities = changes is not None and not changes["added"] and not changes["removed"]
            if not (same_entities and UI in base_files):
                jobs["ui"] = _submit(pool, _design_ui, llm, state, spec)
        if todo and llm is not None:
            progress("generate", f"Writing the business logic of {', '.join(e.name for e in todo)}, the look and the rule tests…")

        if llm is None:
            ledger.append(entry("code_generator", f"rendered {len(files)} files from templates", "deterministic codegen",
                                files=sorted(files)))
        elif not todo:
            ledger.append(entry("code_generator", "no business logic to write",
                                "no custom actions: the verified templates are the whole backend"))
        for done, e in enumerate([e for e in todo if e.name in jobs], 1):
            new_files, rationale, usage = _result(jobs[e.name], ({}, "took too long; stubs kept", Usage()))
            progress("generate", f"Wrote {e.name} ({done}/{len(todo)})")
            total.add(usage)
            files.update(new_files)
            names = ", ".join(a.name for a in spec.actions if a.entity == e.name)
            ledger.append(entry("code_generator",
                                f"wrote the business logic of {e.name} ({names})" if new_files else f"kept the stubs of {e.name}",
                                rationale, files=sorted(new_files), tokens=usage.total_tokens, model=model_names(usage)))
        if llm is not None:
            if "ui" in jobs:
                ui, rationale, usage = _result(jobs["ui"], (None, "took too long; kept the default look", Usage()))
            else:
                ui, rationale, usage = base_files[UI], "same entities as before: the app keeps its look", Usage()
            total.add(usage)
            if ui:
                files[UI] = ui
            ledger.append(entry("code_generator", "designed the web app (name, colours, menu, dashboard)" if ui
                                else "kept the default web app look", rationale, files=[UI] if ui else [],
                                tokens=usage.total_tokens, model=model_names(usage)))
        rule_tests = {}
        if "rules" in jobs:
            rule_tests = _result(jobs["rules"], {})
            ledger.extend(test_ledger)
            total.add(test_usage)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    out: dict[str, Any] = {"status": "generated", "iteration": 0, "ledger": ledger}
    if rule_tests.get(RULE_TESTS) and llm is None and spec.actions:
        # Template mode writes no business logic, so the tests of custom actions (501) are set aside.
        kept_tests = rule_tests[RULE_TESTS]
        for name in re.findall(r"^def (test_\w+)", kept_tests, re.MULTILINE):
            if touches_action(get_test_source(kept_tests, name) or "", spec):
                kept_tests = remove_test(kept_tests, name)
        rule_tests = {RULE_TESTS: kept_tests}
        ledger.append(entry("code_generator", "set aside the rule tests of custom actions",
                            "template mode writes no business logic, so actions answer 501"))
    if rule_tests and _opts(state).get("rule_tests") is False:
        out["held_out"] = rule_tests  # ablation study: written, but only used afterwards to count escaped bugs
    else:
        files.update(rule_tests)
    out["files"] = files
    if total.calls:
        out["usage"] = total.as_state()
    return out


def _result(job: Any, default: Any) -> Any:
    """A parallel job's result, or `default` when it is still not done after JOB_TIMEOUT (never wait forever)."""
    from concurrent.futures import TimeoutError as Late

    try:
        return job.result(timeout=3 * settings.llm_call_deadline_s)
    except Late:
        log.warning("a code generation job missed its deadline; continuing without it")
        return default


def _design_ui(llm: Any, state: AgentState, spec: ProjectSpec) -> tuple[str | None, str, Usage]:
    """The look of the generated web app, as validated JSON (the app code itself is fixed and tested)."""
    user = prompts.UI_USER.format(prompt=state["prompt"], spec=spec.model_dump_json(include={"project_name", "entities"}),
                                  default=json.dumps(default_ui(spec)))
    try:
        ui, usage = structured(llm, "cheap", prompts.UI_SYSTEM, user, UIConfig, effort="low")
    except LLMError as exc:
        return None, f"kept the default look ({exc})", Usage()
    return json.dumps(ui.model_dump(), indent=1) + "\n", "the app's name, colours and dashboard fit the domain", usage


def _service_context(spec: ProjectSpec, e: EntitySpec, files: dict[str, str]) -> str:
    """Read-only code the business logic may need: every model, this entity's schemas, the error type."""
    paths = [f"app/models/{x.module}.py" for x in spec.entities] + [f"app/schemas/{e.module}.py", "app/errors.py"]
    return "\n\n".join(f"# {p}\n```python\n{files[p]}```" for p in paths if p in files)


def _generate_service(
    llm: Any, state: AgentState, spec: ProjectSpec, e: EntitySpec, files: dict[str, str], writer: Any = None
) -> tuple[dict[str, str], str, Usage]:
    """Implement the custom actions of one entity: the only code the model writes."""
    path = f"app/services/{e.module}.py"
    stack = get_stack(spec.stack)
    system = prompts.service_system(stack.name, stack.label, e.module)
    notes = plugins.prompt_notes(_plugins(state), stack.name)
    if notes:
        system += f"\n\nAlso follow these project rules:\n{notes}"
    system += _rules(state)
    user = prompts.SERVICE_USER.format(
        prompt=state["prompt"],
        requirements=requirements_text(spec.requirements),
        spec=spec.model_dump_json(indent=1, exclude={"requirements"}),
        module=e.module,
        stub=files[path],
        context=_service_context(spec, e, files),
    )
    live = LiveText(writer, "code_generator", path)
    try:
        text, usage = ask(llm, "strong", system, user, effort="medium", stream=live)
    except LLMError as exc:
        return {}, f"LLM unavailable ({exc}); stubs kept", Usage()
    finally:
        live.done()
    body = parse_file_blocks(text).get(path, "")
    m = re.search(r"RATIONALE:\s*(.+)", text)
    rationale = m.group(1).strip() if m else "business logic written"
    if not body.strip() and "### FILE:" in text:
        # The reply was cut off before the file ended (an output limit): ask once more for a compact version.
        try:
            text, more = ask(llm, "strong", system, user + prompts.CUT_OFF_RETRY, effort="low")
            usage.add(more)
            body = parse_file_blocks(text).get(path, "")
            rationale = "the first reply was cut off, so it was asked again; " + rationale
        except LLMError:
            pass
    if body.strip():
        body, note = _fix_syntax(llm, path, body, usage)
        rationale += note
    return ({path: body} if body.strip() else {}), rationale, usage


def _fix_syntax(llm: Any, path: str, body: str, usage: Usage) -> tuple[str, str]:
    """A syntax error is fixed right away with one quick call, instead of a full validation round."""
    try:
        ast.parse(body)
        return body, ""
    except SyntaxError as exc:
        error = f"{exc.msg} (line {exc.lineno}): {(exc.text or '').strip()}"
    try:
        text, more = ask(llm, "cheap", prompts.SYNTAX_FIX_SYSTEM, prompts.SYNTAX_FIX_USER.format(
            path=path, error=error, content=body), effort="low")
    except LLMError:
        return body, ""
    usage.add(more)
    fixed = parse_file_blocks(text).get(path, "")
    try:
        ast.parse(fixed)
    except SyntaxError:
        return body, f"; a syntax error remained ({error})"
    return fixed, f"; fixed a syntax error before validation ({error})"


# --------------------------------------------------------------------------- validate


def validate(state: AgentState) -> dict[str, Any]:
    """Static checks first (no subprocess), then the sandbox gates: import -> boot -> tests."""
    spec = _spec(state)
    files = state["files"]
    findings = static_check(files) + plugins.extra_checks(_plugins(state), files)
    blocking = [f.log_line() for f in findings if f.severity == "high"] + check_frontend(files, spec)
    if blocking:
        result = GateResult(False, "static", "\n".join(blocking), 0.0)
    else:
        progress("validate", "Importing and starting the app, then running every test…")
        result = get_sandbox().validate(files, spec.stack)
    validation = result.as_state()
    history = [*state.get("heal_history", []), history_entry(validation, files)]
    bugs = update_bugs(state.get("bugs", []), validation)
    it = state.get("iteration", 0)
    action = f"all gates passed in {result.duration_s:.1f}s" if result.ok else f"failed at gate '{result.gate}' after {result.duration_s:.1f}s"
    ledger = [entry("validator", action, "static checks -> import -> boot -> contract, edge-case and rule tests", iteration=it)]
    if _opts(state).get("resume") and not state.get("heal_history"):
        ledger.insert(0, entry("validator", "kept fixing the previous version's code",
                               "same design and code; healing starts again from where it stopped"))
    out: dict[str, Any] = {}
    if not result.ok and best_index(history) == len(history) - 1:  # the best version so far: remember it
        out = {"best_files": dict(files), "best_validation": validation}
    return {
        **out,
        "validation": validation,
        "heal_history": history,
        "bugs": bugs,
        "review": [f.as_dict() for f in findings if f.severity == "low"],
        "status": "validated" if result.ok else "validation_failed",
        "ledger": ledger,
    }


def _keep_healing(state: AgentState) -> tuple[bool, str]:
    """Ask healing.should_continue with this run's numbers.

    The ablation study can replace the dynamic rule with the usual fixed retry count
    (option fixed_repairs=N): then the run gets exactly N repairs, progress or not.
    """
    fixed = _opts(state).get("fixed_repairs")
    if fixed is not None:
        attempts = max(len(state.get("heal_history", [])) - 1, 0)
        return (True, f"fixed policy: repair {attempts + 1} of {fixed}") if attempts < fixed else (
            False, f"fixed limit of {fixed} repair attempts reached")
    safety, budget = scaled_limits(_spec(state), settings.max_heal_safety, settings.max_cost_per_run_usd)
    return should_continue(
        state.get("heal_history", []),
        tokens_used=int((state.get("usage") or {}).get("total_tokens", 0)),
        token_budget=int(settings.max_tokens_per_run * budget / settings.max_cost_per_run_usd),
        llm_available=get_llm() is not None,
        safety_limit=safety,
        budget_problem=budget_left(state.get("usage"), budget),
    )


def route_after_validate(state: AgentState) -> str:
    if state["validation"]["ok"]:
        return "package"
    keep_going, _ = _keep_healing(state)
    return "classify" if keep_going else "failure_report"


# --------------------------------------------------------------------------- heal


def classify(state: AgentState) -> dict[str, Any]:
    spec = _spec(state)
    v = state["validation"]
    error_class = classify_error(v["log"])
    path = repair_target(locate_failing_file(v["log"], state["files"], spec), state["files"])
    if path is None or not editable(path):  # nothing in the log points at the business logic: best guess
        path = guess_service(v["log"], state["files"], spec) or path
    return {
        "error_class": error_class,
        "validation": {**v, "failing_file": path},
        "status": "classified",
        "ledger": [
            entry(
                "error_classifier",
                f"classified as '{error_class}'" + (f" in {path}" if path else ""),
                prompts.ERROR_HINTS[error_class],
                files=[path] if path else [],
                iteration=state.get("iteration", 0),
            )
        ],
    }


def reflect(state: AgentState) -> dict[str, Any]:
    """Repair the failing file, escalating only when the previous attempt made no progress.

    The rung comes from healing.repair_rung: the cheap model while we are making
    progress, the strong model after a stall, and the verified templates after a
    second stall (trades the LLM's improvements for a known-good build). The model
    may instead dispute an AI-written test; the Judge then decides.
    """
    v = state["validation"]
    files = state["files"]
    attempt = state.get("iteration", 0) + 1
    history = state.get("heal_history", [])
    if history and history[-1].get("transient"):
        # The machine was busy (sandbox limits, a timeout with no test result): change nothing, wait, test again.
        busy = sum(1 for h in reversed(history) if h.get("transient"))
        time.sleep(SANDBOX_RETRY_WAIT_S * busy)
        note = entry("reflector", f"attempt {attempt}: the machine was busy; testing again unchanged",
                     prompts.ERROR_HINTS["sandbox"], iteration=attempt)
        return {"files": {}, "iteration": attempt, "status": "repaired", "ledger": [note]}
    rollback: dict[str, str] = {}
    if regressed(history) and state.get("best_files"):
        # The last repair made things worse: start again from the best version, with its failures.
        rollback = {p: c for p, c in state["best_files"].items() if files.get(p) != c}
        files = {**files, **state["best_files"]}
        spec = _spec(state)
        best_v = state.get("best_validation") or v
        target = repair_target(locate_failing_file(best_v["log"], files, spec), files)
        if target is None or not editable(target):
            target = guess_service(best_v["log"], files, spec) or target
        v = {**best_v, "failing_file": target}
        state = {**state, "files": files, "validation": v}
    path = v.get("failing_file")
    usage = Usage()
    changed: dict[str, str] = {}
    diagnosis = ""
    agent = "reflector"
    tool_calls: list[dict[str, Any]] = []

    llm = get_llm()
    rung = repair_rung(state.get("heal_history", []), llm is not None)
    if llm is not None and path and rung in ("cheap", "strong"):
        try:
            if attempt > 1:  # the first repair always happens
                check_budget(state.get("usage"),
                             scaled_limits(_spec(state), settings.max_heal_safety, settings.max_cost_per_run_usd)[1])
            targets = failing_targets(v["log"], files, _spec(state), path, limit=max(1, min(3, settings.llm_max_parallel)))
            targets = targets or [path]
            progress("reflect", f"Repairing {', '.join(targets)} with the {rung} model…")
            agent = f"reflector ({rung})"
            text, usage, changed, diagnosis, tool_calls = _repair_files(llm, rung, state, targets, attempt)
            dispute = parse_dispute(text)
            if not changed and dispute:
                changed, diagnosis, agent, more = _settle_dispute(llm, state, *dispute)
                usage.add(more)
        except LLMError as exc:
            diagnosis = f"LLM repair unavailable ({exc})"

    if not changed and path and rung == "template":
        changed = _template_restore(path, files, state.get("template_files", {}))
        if changed:
            agent = "template_repair"
            diagnosis = (diagnosis + "; " if diagnosis else "") + f"restored {', '.join(changed)} from verified templates"
    if not changed:
        diagnosis = diagnosis or "no repair candidate found"
    if rollback:
        diagnosis = f"rolled back the previous repair (it made things worse); {diagnosis}"

    out: dict[str, Any] = {
        "files": {**rollback, **changed},
        "iteration": attempt,
        "status": "repaired" if changed or rollback else "repair_failed",
        "ledger": [
            entry(
                agent,
                f"attempt {attempt}: patched {', '.join(changed) or 'nothing'}",
                diagnosis,
                files=sorted(changed),
                tokens=usage.total_tokens,
                iteration=attempt,
                model=model_names(usage),
                diff={p: diff(p, files.get(p, ""), body) for p, body in changed.items()},
                tools=tool_calls,
            )
        ],
    }
    if usage.calls:
        out["usage"] = usage.as_state()
    return out


def _failing_test_sources(log: str, files: dict[str, str], limit: int = 3) -> str:
    """The code of the first few failing tests, so the model sees what is expected."""
    parts = []
    for path, name in failing_tests(log)[:limit]:
        code = get_test_source(files.get(path, ""), name)
        if code:
            parts.append(f"# {path}::{name}\n```python\n{code}\n```")
    return "\n\n".join(parts) or "(none)"


def _llm_repair(llm: Any, role: str, state: AgentState, path: str, attempt: int) -> tuple[str, Usage, Toolbox | None]:
    """Ask the model for a repair; with tools (the default) it can read, search and run tests first."""
    spec = _spec(state)
    v = state["validation"]
    files = state["files"]
    error_class = state.get("error_class", "unknown")
    related = "\n\n".join(f"# {p}\n```python\n{files[p]}```" for p in related_files(path, files))
    related += "\n\nFailing tests:\n" + _failing_test_sources(v["log"], files)
    user = prompts.REFLECT_USER.format(
        gate=v["gate"],
        error_class=error_class,
        hint=prompts.ERROR_HINTS[error_class],
        attempt=attempt,
        requirements=requirements_text(spec.requirements),
        log=v["log"][-4000:],
        path=path,
        content=files[path],
        related=related,
    )
    system = prompts.reflect_system(spec.stack)
    notes = plugins.prompt_notes(_plugins(state), spec.stack)
    if notes:
        system += f"\n- Also follow these project rules:\n{notes}"
    system += _rules(state)
    if _opts(state).get("tools", True) and hasattr(llm, "complete_with_tools"):
        box = Toolbox(files, spec, extra=plugins.tools(_plugins(state)) + load_mcp_tools())
        try:
            text, usage = llm.complete_with_tools(role, system + prompts.REFLECT_TOOLS.format(max_calls=box.max_calls), user, box)
            return text, usage, box
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - e.g. a model without function calling: repair without tools
            log.warning("tool-using repair failed (%s); repairing without tools", exc)
    text, usage = ask(llm, role, system, user, effort="high" if role == "strong" else "low")
    return text, usage, None


def _repair_files(llm: Any, rung: str, state: AgentState, targets: list[str], attempt: int
                  ) -> tuple[str, Usage, dict[str, str], str, list[dict[str, Any]]]:
    """Repair one file, or several independent ones at the same time (each with its own tools and tests).

    Returns (the first reply, usage, changed files, diagnosis, tool calls)."""
    files = state["files"]

    def one(path: str) -> tuple[str, Usage, dict[str, str], str, list[dict[str, Any]]]:
        text, usage, box = _llm_repair(llm, rung, state, path, attempt)
        changed, diagnosis = _parse_repair(text, files)
        if box is not None:
            changed = {**box.changes(), **changed}  # staged with write_file, unless the answer repeats it
        return text, usage, changed, diagnosis, (box.calls if box is not None else [])

    if len(targets) == 1:
        return one(targets[0])
    pool = ThreadPoolExecutor(max_workers=len(targets))
    try:
        jobs = [_submit(pool, one, path) for path in targets]
        results = []
        for path, job in zip(targets, jobs, strict=True):
            try:
                results.append((path, job.result(timeout=3 * settings.llm_call_deadline_s)))
            except LLMError:
                raise
            except Exception as exc:  # noqa: BLE001 - one target failing must not lose the others' repairs
                log.warning("repair of %s failed: %s", path, exc)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if not results:
        raise LLMError("every repair failed")
    usage, changed, notes, calls = Usage(), {}, [], []
    for path, (_, used, files_changed, diagnosis, tool_calls) in results:
        usage.add(used)
        changed.update(files_changed)
        calls += tool_calls
        if diagnosis:
            notes.append(f"{path}: {diagnosis}")
    return results[0][1][0], usage, changed, "; ".join(notes), calls


def _parse_repair(text: str, files: dict[str, str]) -> tuple[dict[str, str], str]:
    """Replacement files from a Reflector reply (never tests), plus its diagnosis."""
    changed = {p: body for p, body in parse_file_blocks(text).items() if p in files and editable(p) and body != files[p]}
    m = re.search(r"DIAGNOSIS:\s*(.+)", text)
    return changed, (m.group(1).strip() if m else "")


def _settle_dispute(llm: Any, state: AgentState, test: str, argument: str) -> tuple[dict[str, str], str, str, Usage]:
    """The Reflector says an AI-written test is wrong: the Judge decides; a wrong test is removed."""
    files = state["files"]
    failing = {name: path for path, name in failing_tests(state["validation"]["log"])}
    path = failing.get(test) or next((p for n, p in failing.items() if n.split("[")[0] == test.split("[")[0]), None)
    if path not in AI_TEST_FILES:
        return {}, f"dispute rejected: {test} is a contract test and cannot be removed", "judge", Usage()
    code = get_test_source(files[path], test) or ""
    wrong, reason, usage = judge(llm, _spec(state), path, test, code, argument, state["validation"]["log"])
    if not wrong:
        return {}, f"judge kept {test}: {reason}", "judge", usage
    return {path: remove_test(files[path], test)}, f"judge removed {test}: {reason}", "judge", usage


def _template_restore(path: str, files: dict[str, str], templates: dict[str, str]) -> dict[str, str]:
    """Restore the failing file and its entity siblings, so model/schema/router stay consistent."""
    candidates = [path, *related_files(path, files)]
    return {p: templates[p] for p in candidates if p in templates and templates[p] != files.get(p)}


# --------------------------------------------------------------------------- outputs


def package(state: AgentState) -> dict[str, Any]:
    """Generate real migration files, refresh the README, and zip the project with its ledger."""
    spec = _spec(state)
    files = state["files"]
    ledger: list[dict[str, Any]] = []
    new_files, note = get_sandbox().make_migrations(files, spec.stack)
    ledger.append(entry("devops_packager", note, "the framework's own migration tool, applied once to prove it works",
                        files=sorted(new_files)))
    new_files["README.md"] = render_project(
        spec, has_rule_tests=bool(files.get(RULE_TESTS)), has_edge_tests=bool(files.get(EDGE_TESTS))
    )["README.md"]
    done = entry("devops_packager", "packaged project + ledger as zip", "validated output, ready to run",
                 iteration=state.get("iteration", 0))
    ledger.append(done)
    final = {**state, "files": {**files, **new_files}, "ledger": [*state.get("ledger", []), *ledger]}
    path = write_artifact(final, spec.project_name, {"VIBESTACK_LEDGER.md": ledger_markdown(final)})
    return {"files": new_files, "artifact_path": path, "status": "succeeded", "ledger": ledger}


def failure_report(state: AgentState) -> dict[str, Any]:
    v = state["validation"]
    _, reason = _keep_healing(state)
    if state.get("error_class") == "sandbox":
        reason = "the sandbox ran out of threads or processes (a server limit, not a bug in your app); try again"
    report = (
        f"# Generation failed validation\n\n"
        f"Self-healing stopped after {state.get('iteration', 0)} repair attempts: **{reason}**.\n\n"
        f"- Failed gate: `{v['gate']}`\n- Error class: `{state.get('error_class', 'unknown')}`\n"
        f"- Suspected file: `{v.get('failing_file')}`\n\n## Last log\n\n```\n{v['log'][-3000:]}\n```\n"
    )
    stop = entry("circuit_breaker", "stopped healing; produced diagnostic report", reason)
    shipped: dict[str, str] = {}
    if regressed(state.get("heal_history", [])) and state.get("best_files"):
        # Ship the best version reached, not whatever the last repair left.
        shipped = {p: c for p, c in state["best_files"].items() if state["files"].get(p) != c}
        stop["rationale"] += "; the zip holds the best version reached, not the last attempt"
    final = {**state, "files": {**state["files"], **shipped}, "ledger": [*state.get("ledger", []), stop]}
    path = write_artifact(final, _spec(state).project_name,
                          {"VIBESTACK_LEDGER.md": ledger_markdown(final), "VIBESTACK_FAILURE.md": report})
    return {"artifact_path": path, "report": report, "stop_reason": reason, "status": "failed", "ledger": [stop],
            **({"files": shipped} if shipped else {})}


__all__ = [
    "classify",
    "failure_report",
    "generate",
    "package",
    "parse_spec",
    "plan",
    "reflect",
    "route_after_validate",
    "validate",
]
