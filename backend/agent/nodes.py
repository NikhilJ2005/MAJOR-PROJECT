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

import json
import logging
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import plugins, prompts
from .analysis import check_coverage, requirements_text
from .bughunt import failing_tests, static_check, update_bugs
from .codegen import plan_files, render_project
from .config import settings
from .frontend import UI, UIConfig, check_frontend, default_ui
from .healing import (
    classify_error,
    history_entry,
    locate_failing_file,
    related_files,
    repair_rung,
    should_continue,
)
from .ledger import diff, entry, ledger_markdown, model_names, write_artifact
from .llm import LLMError, Usage, check_budget, get_llm, parse_file_blocks, structured
from .mcp_tools import load_tools as load_mcp_tools
from .sandbox import GateResult, get_sandbox
from .stacks import entity_files, get_stack
from .state import AgentState, EntitySpec, ProjectSpec
from .testwriter import (
    AI_TEST_FILES,
    EDGE_TESTS,
    RULE_TESTS,
    get_test_source,
    judge,
    needs_rule_tests,
    parse_dispute,
    remove_test,
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
    return plugins.chosen(_opts(state).get("plugins"))


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

    def ask(text: str, usage: Usage | None = None) -> tuple[ProjectSpec, Usage]:
        spec, used = structured(llm, "strong", prompts.SPEC_SYSTEM, text, ProjectSpec)
        if usage:
            used.add(usage)
        raw = spec.model_dump()
        if base:
            raw["project_name"] = base.project_name  # an edit never renames the project
        return _with_stack(ProjectSpec.model_validate(raw), stack), used

    spec, usage = ask(user)
    missing = check_coverage(spec)
    if missing:
        spec, usage = ask(user + "\n\n" + prompts.SPEC_COVERAGE_RETRY.format(missing=", ".join(missing)), usage)
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


def _rule_tests(llm: Any, spec: ProjectSpec, ledger: list[dict[str, Any]], usage: Usage) -> dict[str, str]:
    """Call 2: business-rule tests from the requirements, written before any code exists."""
    if not needs_rule_tests(spec):
        return {}
    try:
        source, more = write_rule_tests(llm, spec)
    except LLMError as exc:
        ledger.append(entry("architect", "no rule tests written", str(exc)))
        return {}
    usage.add(more)
    if source is None:
        ledger.append(entry("architect", "rule-test reply was not usable", "relying on contract + edge-case tests"))
        return {}
    ledger.append(entry("architect", f"wrote {_count_tests(source)} business-rule tests",
                        "from the requirements only, before any code exists", files=[RULE_TESTS]))
    return {RULE_TESTS: source}


def parse_spec(state: AgentState) -> dict[str, Any]:
    """Architect: prompt -> requirements + a validated design (+ rule tests written before the code)."""
    stack = _opts(state).get("stack")
    if state.get("spec"):
        spec = _with_stack(ProjectSpec.model_validate(state["spec"]), stack)
        return {
            "spec": spec.model_dump(),
            "status": "spec_ready",
            "ledger": [entry("architect", f"used provided spec ({spec.stack})", "spec supplied with the request")],
        }
    llm = _require_llm()
    spec, missing, usage = _design(llm, state)
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
    ledger = [entry("architect", action, coverage, model=model_names(usage))]
    files = _rule_tests(llm, spec, ledger, usage)
    ledger[0]["tokens"] = usage.total_tokens
    out: dict[str, Any] = {"spec": spec.model_dump(), "status": "spec_ready", "usage": usage.as_state(), "ledger": ledger}
    if files:
        out["files"] = files
    if changes is not None:
        out["changes"] = changes
    return out


# --------------------------------------------------------------------------- plan + generate


def plan(state: AgentState) -> dict[str, Any]:
    spec = _spec(state)
    file_plan = plan_files(spec)
    template_files = render_project(spec)
    if _opts(state).get("edge_tests") is False:  # ablation study: run without the spec-derived edge-case tests
        template_files.pop(EDGE_TESTS, None)
        file_plan = [f for f in file_plan if f["path"] != EDGE_TESTS]
    ledger = [
        entry(
            "planner",
            f"planned {len(file_plan)} {get_stack(spec.stack).label} files in dependency order",
            "models -> schemas -> services -> routers -> app wiring -> tests -> packaging",
        )
    ]
    for name, added in plugins.extra_files(_plugins(state), spec).items():
        added = {p: c for p, c in added.items() if p not in template_files}  # plugins add, never overwrite
        template_files.update(added)
        file_plan += [{"path": p, "owner": f"plugin:{name}", "purpose": f"added by the {name} plugin"} for p in sorted(added)]
        if added:
            ledger.append(entry(f"plugin:{name}", f"added {len(added)} file(s)", "extra_files hook", files=sorted(added)))
    return {
        "file_plan": file_plan,
        "template_files": template_files,
        "status": "planned",
        "ledger": ledger,
    }


def generate(state: AgentState) -> dict[str, Any]:
    spec = _spec(state)
    files = dict(state["template_files"])
    ledger: list[dict[str, Any]] = []
    total = Usage()
    mode = _opts(state).get("codegen_mode") or settings.codegen_mode
    llm = get_llm() if mode == "llm" else None

    # Follow-up edit: entities the Architect did not touch keep the code of the previous version.
    changes, base_files = state.get("changes"), state.get("base_files") or {}
    todo = spec.entities
    if changes is not None:
        touched = set(changes["added"]) | set(changes["changed"])
        kept = [e for e in spec.entities if e.name not in touched]
        for e in kept:
            files.update({p: base_files[p] for p in entity_files(e) if p in base_files})
        todo = [e for e in spec.entities if e.name in touched]
        if kept:
            ledger.append(entry("code_generator", f"kept {', '.join(e.name for e in kept)} from version before",
                                "unchanged by the edit, so their code (and its fixes) is reused"))

    if llm is None:
        ledger.append(entry("code_generator", f"rendered {len(files)} files from templates", "deterministic codegen", files=sorted(files)))
    else:
        def work(e: EntitySpec) -> tuple[EntitySpec, dict[str, str], str, Usage]:
            return (e, *_generate_entity(llm, state, spec, e, files))

        with ThreadPoolExecutor(max_workers=4) as pool:
            for e, new_files, rationale, usage in pool.map(work, todo):
                total.add(usage)
                files.update(new_files)
                ledger.append(
                    entry(
                        "code_generator",
                        f"wrote {e.name} model/schema/service/router" if new_files else f"kept template for {e.name}",
                        rationale,
                        files=sorted(new_files),
                        tokens=usage.total_tokens,
                        model=model_names(usage),
                    )
                )

        same_entities = changes is not None and not changes["added"] and not changes["removed"]
        if same_entities and UI in base_files:
            ui, rationale, usage = base_files[UI], "same entities as before: the app keeps its look", Usage()
        else:
            ui, rationale, usage = _design_ui(llm, state, spec)
        total.add(usage)
        if ui:
            files[UI] = ui
        ledger.append(entry("code_generator", "designed the web app (name, colours, menu, dashboard)" if ui else "kept the default web app look",
                            rationale, files=[UI] if ui else [], tokens=usage.total_tokens, model=model_names(usage)))

    out: dict[str, Any] = {"files": files, "status": "generated", "iteration": 0, "ledger": ledger}
    if total.calls:
        out["usage"] = total.as_state()
    return out


def _design_ui(llm: Any, state: AgentState, spec: ProjectSpec) -> tuple[str | None, str, Usage]:
    """The look of the generated web app, as validated JSON (the app code itself is fixed and tested)."""
    user = prompts.UI_USER.format(prompt=state["prompt"], spec=spec.model_dump_json(include={"project_name", "entities"}),
                                  default=json.dumps(default_ui(spec)))
    try:
        ui, usage = structured(llm, "cheap", prompts.UI_SYSTEM, user, UIConfig)
    except LLMError as exc:
        return None, f"kept the default look ({exc})", Usage()
    return json.dumps(ui.model_dump(), indent=1) + "\n", "the app's name, colours and dashboard fit the domain", usage


def _generate_entity(
    llm: Any, state: AgentState, spec: ProjectSpec, e: EntitySpec, baseline: dict[str, str]
) -> tuple[dict[str, str], str, Usage]:
    expected = entity_files(e)
    stack = get_stack(spec.stack)
    reference = "\n\n".join(f"### FILE: {p}\n```python\n{baseline[p]}```" for p in expected)
    system = prompts.entity_system(stack.name, stack.label, e.module, e.name, e.table)
    notes = plugins.prompt_notes(_plugins(state), stack.name)
    if notes:
        system += f"\n\nAlso follow these project rules:\n{notes}"
    user = prompts.ENTITY_USER.format(
        prompt=state["prompt"],
        requirements=requirements_text(spec.requirements),
        spec=spec.model_dump_json(indent=1, exclude={"requirements"}),
        name=e.name,
        reference=reference,
    )
    try:
        text, usage = llm.complete("strong", system, user)
    except LLMError as exc:
        return {}, f"LLM unavailable ({exc}); template kept", Usage()
    blocks = parse_file_blocks(text)
    new_files = {p: blocks[p] for p in expected if p in blocks and blocks[p].strip()}
    m = re.search(r"RATIONALE:\s*(.+)", text)
    return new_files, (m.group(1).strip() if m else "entity files generated"), usage


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
        result = get_sandbox().validate(files, spec.stack)
    validation = result.as_state()
    history = [*state.get("heal_history", []), history_entry(validation, files)]
    bugs = update_bugs(state.get("bugs", []), validation)
    it = state.get("iteration", 0)
    action = f"all gates passed in {result.duration_s:.1f}s" if result.ok else f"failed at gate '{result.gate}' after {result.duration_s:.1f}s"
    ledger = [entry("validator", action, "static checks -> import -> boot -> contract, edge-case and rule tests", iteration=it)]
    return {
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
    return should_continue(
        state.get("heal_history", []),
        tokens_used=int((state.get("usage") or {}).get("total_tokens", 0)),
        token_budget=settings.max_tokens_per_run,
        llm_available=get_llm() is not None,
        safety_limit=settings.max_heal_safety,
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
    path = locate_failing_file(v["log"], state["files"], spec)
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
    if state.get("error_class") == "sandbox":
        # The server ran out of threads/processes, not the code: change nothing, wait, validate again.
        time.sleep(SANDBOX_RETRY_WAIT_S)
        note = entry("reflector", f"attempt {attempt}: sandbox limit hit; re-running validation",
                     prompts.ERROR_HINTS["sandbox"], iteration=attempt)
        return {"files": {}, "iteration": attempt, "status": "repaired", "ledger": [note]}
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
            check_budget(state.get("usage"))
            text, usage, box = _llm_repair(llm, rung, state, path, attempt)
            agent = f"reflector ({rung})"
            changed, diagnosis = _parse_repair(text, files)
            if box is not None:
                changed = {**box.changes(), **changed}  # staged with write_file, unless the answer repeats it
                tool_calls = box.calls
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

    out: dict[str, Any] = {
        "files": changed,
        "iteration": attempt,
        "status": "repaired" if changed else "repair_failed",
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
    if _opts(state).get("tools", True) and hasattr(llm, "complete_with_tools"):
        box = Toolbox(files, spec, extra=plugins.tools(_plugins(state)) + load_mcp_tools())
        try:
            text, usage = llm.complete_with_tools(role, system + prompts.REFLECT_TOOLS.format(max_calls=box.max_calls), user, box)
            return text, usage, box
        except LLMError:
            raise
        except Exception as exc:  # noqa: BLE001 - e.g. a model without function calling: repair without tools
            log.warning("tool-using repair failed (%s); repairing without tools", exc)
    text, usage = llm.complete(role, system, user)
    return text, usage, None


def _parse_repair(text: str, files: dict[str, str]) -> tuple[dict[str, str], str]:
    """Replacement files from a Reflector reply (never tests), plus its diagnosis."""
    changed = {
        p: body for p, body in parse_file_blocks(text).items()
        if p in files and not p.startswith("tests/") and body != files[p]
    }
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
    final = {**state, "ledger": [*state.get("ledger", []), stop]}
    path = write_artifact(final, _spec(state).project_name,
                          {"VIBESTACK_LEDGER.md": ledger_markdown(final), "VIBESTACK_FAILURE.md": report})
    return {"artifact_path": path, "report": report, "stop_reason": reason, "status": "failed", "ledger": [stop]}


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
