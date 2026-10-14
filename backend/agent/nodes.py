"""LangGraph nodes: one function per agent. Each takes the state and returns the part it changed.

    analyze      Requirements Analyst   prompt -> numbered requirements
    parse_spec   Architect              requirements -> ProjectSpec (entities, access, actions)
    plan         Planner                file plan + verified templates for the chosen framework
    write_tests  Test Engineer          business-rule tests, written before any code exists
    generate     API Engineer           per-entity code (models, schemas, services, routers)
    validate     Validator              static checks -> import -> boot -> tests
    bug_hunt     Bug Hunter             adversarial tests once the contract passes
    classify     Error Classifier       which kind of failure, in which file
    reflect      Reflector (+ Judge)    repair, escalating only when there is no progress
    hunt_giveup                         keep the last passing code if an edge case can't be fixed
    package      Packager               migrations, README, zip
    failure_report  Circuit breaker     diagnostic report when healing stops making progress

Every node appends to the change ledger, which makes a run explainable after the fact.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import plugins, prompts
from .analysis import RequirementList, check_coverage, requirements_text
from .bughunt import failing_tests, static_check, update_bugs, write_edge_tests
from .codegen import plan_files, render_project
from .config import settings
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

log = logging.getLogger(__name__)


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
        raise LLMError("OPENROUTER_API_KEY is not set and no spec was provided")
    return llm


# --------------------------------------------------------------------------- requirements + design


def analyze(state: AgentState) -> dict[str, Any]:
    """Requirements Analyst: split the prompt into small, testable requirements."""
    if state.get("spec"):
        reqs = state["spec"].get("requirements") or []
        return {
            "requirements": reqs,
            "status": "analyzed",
            "ledger": [entry("analyst", f"used {len(reqs)} requirements from the provided spec", "spec supplied with the request")],
        }
    result, usage = structured(
        _require_llm(), "strong", prompts.ANALYST_SYSTEM, prompts.ANALYST_USER.format(prompt=state["prompt"]), RequirementList
    )
    reqs = [r.model_dump() for r in result.requirements]
    kinds = Counter(r["kind"] for r in reqs)
    summary = ", ".join(f"{n} {kind}" for kind, n in kinds.most_common())
    return {
        "requirements": reqs,
        "status": "analyzed",
        "usage": usage.as_state(),
        "ledger": [
            entry("analyst", f"listed {len(reqs)} requirements ({summary})", "a long request becomes small, testable pieces",
                  tokens=usage.total_tokens, model=model_names(usage))
        ],
    }


def _finish_spec(spec: ProjectSpec, requirements: list[dict], stack: str | None) -> ProjectSpec:
    """Attach the Analyst's requirements and the chosen framework to the Architect's design."""
    raw = spec.model_dump()
    raw["requirements"] = requirements
    raw["stack"] = stack or raw.get("stack") or "fastapi"
    return ProjectSpec.model_validate(raw)


def parse_spec(state: AgentState) -> dict[str, Any]:
    """Architect: requirements -> a validated ProjectSpec, checked for full requirement coverage."""
    stack = _opts(state).get("stack")
    if state.get("spec"):
        spec = _finish_spec(ProjectSpec.model_validate(state["spec"]), state.get("requirements") or [], stack)
        return {
            "spec": spec.model_dump(),
            "status": "spec_ready",
            "ledger": [entry("architect", f"used provided spec ({spec.stack})", "spec supplied with the request")],
        }
    llm = _require_llm()
    requirements = state.get("requirements") or []
    user = prompts.SPEC_USER.format(prompt=state["prompt"], requirements=requirements_text(requirements))
    spec, usage = structured(llm, "strong", prompts.SPEC_SYSTEM, user, ProjectSpec)
    spec = _finish_spec(spec, requirements, stack)
    missing = check_coverage(spec)
    if missing:
        retry = user + "\n\n" + prompts.SPEC_COVERAGE_RETRY.format(missing=", ".join(missing))
        second, more = structured(llm, "strong", prompts.SPEC_SYSTEM, retry, ProjectSpec)
        usage.add(more)
        spec = _finish_spec(second, requirements, stack)
        missing = check_coverage(spec)
    names = ", ".join(e.name for e in spec.entities)
    action = f"designed {len(spec.entities)} entities ({names})"
    action += f", {len(spec.actions)} custom actions" if spec.actions else ""
    action += " + JWT auth" if spec.auth else ""
    coverage = "all requirements covered" if not missing else f"not covered: {', '.join(missing)}"
    return {
        "spec": spec.model_dump(),
        "status": "spec_ready",
        "usage": usage.as_state(),
        "ledger": [entry("architect", action, coverage, tokens=usage.total_tokens, model=model_names(usage))],
    }


# --------------------------------------------------------------------------- plan, tests first, generate


def plan(state: AgentState) -> dict[str, Any]:
    spec = _spec(state)
    file_plan = plan_files(spec)
    template_files = render_project(spec)
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
        "phase": "build",
        "ledger": ledger,
    }


def write_tests(state: AgentState) -> dict[str, Any]:
    """Test Engineer: business-rule tests from the requirements, before any code exists."""
    spec = _spec(state)
    llm = get_llm()
    if llm is None or not needs_rule_tests(spec):
        why = "no model available" if llm is None else "plain CRUD: the contract tests cover it"
        return {"status": "tests_written", "ledger": [entry("test_engineer", "no rule tests written", why)]}
    try:
        check_budget(state.get("usage"))
        source, usage = write_rule_tests(llm, spec)
    except LLMError as exc:
        return {"status": "tests_written", "ledger": [entry("test_engineer", "no rule tests written", str(exc))]}
    out: dict[str, Any] = {"status": "tests_written", "usage": usage.as_state()}
    if source is None:
        out["ledger"] = [entry("test_engineer", "reply was not a usable test file", "relying on the contract tests",
                               tokens=usage.total_tokens, model=model_names(usage))]
        return out
    out["files"] = {RULE_TESTS: source}
    out["ledger"] = [
        entry("test_engineer", f"wrote {_count_tests(source)} business-rule tests", "from the requirements only, before any code exists",
              files=[RULE_TESTS], tokens=usage.total_tokens, model=model_names(usage))
    ]
    return out


def generate(state: AgentState) -> dict[str, Any]:
    spec = _spec(state)
    files = dict(state["template_files"])
    ledger: list[dict[str, Any]] = []
    total = Usage()
    mode = _opts(state).get("codegen_mode") or settings.codegen_mode
    llm = get_llm() if mode == "llm" else None

    if llm is None:
        ledger.append(entry("api_engineer", f"rendered {len(files)} files from templates", "deterministic codegen", files=sorted(files)))
    else:
        def work(e: EntitySpec) -> tuple[EntitySpec, dict[str, str], str, Usage]:
            return (e, *_generate_entity(llm, state, spec, e, files))

        with ThreadPoolExecutor(max_workers=4) as pool:
            for e, new_files, rationale, usage in pool.map(work, spec.entities):
                total.add(usage)
                files.update(new_files)
                ledger.append(
                    entry(
                        "api_engineer",
                        f"wrote {e.name} model/schema/service/router" if new_files else f"kept template for {e.name}",
                        rationale,
                        files=sorted(new_files),
                        tokens=usage.total_tokens,
                        model=model_names(usage),
                    )
                )

    out: dict[str, Any] = {"files": files, "status": "generated", "iteration": 0, "ledger": ledger}
    if total.calls:
        out["usage"] = total.as_state()
    return out


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
    blocking = [f for f in findings if f.severity == "high"]
    if blocking:
        result = GateResult(False, "static", "\n".join(f.log_line() for f in blocking), 0.0)
    else:
        result = get_sandbox().validate(files, spec.stack)
    validation = result.as_state()
    history = [*state.get("heal_history", []), history_entry(validation, files)]
    bugs = update_bugs(state.get("bugs", []), validation)
    it = state.get("iteration", 0)
    action = f"all gates passed in {result.duration_s:.1f}s" if result.ok else f"failed at gate '{result.gate}' after {result.duration_s:.1f}s"
    ledger = [entry("validator", action, "static checks -> import -> boot -> tests", iteration=it)]
    if state.get("phase") == "hunt" and len(history) == 1:
        found = [b for b in bugs if b["found_by"] == "bug hunter" and not b["fixed"]]
        names = ", ".join(b["detail"] for b in found)
        ledger.append(entry("bug_hunter", f"found {len(found)} bug(s)" + (f": {names}" if found else ": every edge case passed"),
                            "failures of its own adversarial tests are real bugs"))
    return {
        "validation": validation,
        "heal_history": history,
        "bugs": bugs,
        "review": [f.as_dict() for f in findings if f.severity == "low"],
        "status": "validated" if result.ok else "validation_failed",
        "ledger": ledger,
    }


def _keep_healing(state: AgentState) -> tuple[bool, str]:
    """Ask healing.should_continue with this run's numbers."""
    return should_continue(
        state.get("heal_history", []),
        tokens_used=int((state.get("usage") or {}).get("total_tokens", 0)),
        token_budget=settings.max_tokens_per_run,
        llm_available=get_llm() is not None,
        safety_limit=settings.max_heal_safety,
    )


def route_after_validate(state: AgentState) -> str:
    phase = state.get("phase", "build")
    if state["validation"]["ok"]:
        hunt = phase == "build" and get_llm() is not None and _opts(state).get("bug_hunt", True)
        return "bug_hunt" if hunt else "package"
    keep_going, _ = _keep_healing(state)
    if keep_going:
        return "classify"
    return "hunt_giveup" if phase == "hunt" else "failure_report"


# --------------------------------------------------------------------------- bug hunt


def bug_hunt(state: AgentState) -> dict[str, Any]:
    """Bug Hunter: adversarial tests against code that already passes its contract."""
    spec = _spec(state)
    out: dict[str, Any] = {"phase": "hunt", "heal_history": [], "safe_files": dict(state["files"]), "status": "hunting"}
    try:
        check_budget(state.get("usage"))
        source, focus, usage = write_edge_tests(_require_llm(), spec, state["files"])
    except LLMError as exc:
        return {**out, "phase": "done", "ledger": [entry("bug_hunter", "skipped", str(exc))]}
    out["usage"] = usage.as_state()
    if source is None:
        out.update(phase="done", ledger=[entry("bug_hunter", "reply was not a usable test file", "nothing to run",
                                               tokens=usage.total_tokens, model=model_names(usage))])
        return out
    out["files"] = {EDGE_TESTS: source}
    out["ledger"] = [
        entry("bug_hunter", f"wrote {_count_tests(source)} adversarial tests", f"probing: {focus}", files=[EDGE_TESTS],
              tokens=usage.total_tokens, model=model_names(usage))
    ]
    return out


def route_after_hunt(state: AgentState) -> str:
    return "validate" if state.get("phase") == "hunt" else "package"


def hunt_giveup(state: AgentState) -> dict[str, Any]:
    """Some edge cases could not be fixed: deliver the last passing code and list them as open issues."""
    files = state["files"]
    failing = [name for path, name in failing_tests(state["validation"]["log"]) if path == EDGE_TESTS]
    edge = files.get(EDGE_TESTS, "")
    for name in failing:
        edge = remove_test(edge, name)
    restored = {p: c for p, c in (state.get("safe_files") or {}).items() if files.get(p) != c}
    restored[EDGE_TESTS] = edge if failing else ""
    bugs = [{**b, "open": True} if b["found_by"] == "bug hunter" and not b["fixed"] else b for b in state.get("bugs", [])]
    _, reason = _keep_healing(state)
    return {
        "files": restored,
        "bugs": bugs,
        "phase": "final",
        "heal_history": [],
        "status": "repaired",
        "ledger": [
            entry("bug_hunter", f"{len(failing) or 'some'} edge case(s) left open; kept the last passing code",
                  f"healing stopped ({reason}); open issues are listed in the ledger", files=sorted(restored))
        ],
    }


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
    path = v.get("failing_file")
    usage = Usage()
    changed: dict[str, str] = {}
    diagnosis = ""
    agent = "reflector"

    llm = get_llm()
    rung = repair_rung(state.get("heal_history", []), llm is not None)
    if llm is not None and path and rung in ("cheap", "strong"):
        try:
            check_budget(state.get("usage"))
            text, usage = _llm_repair(llm, rung, state, path, attempt)
            agent = f"reflector ({rung})"
            changed, diagnosis = _parse_repair(text, files)
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


def _llm_repair(llm: Any, role: str, state: AgentState, path: str, attempt: int) -> tuple[str, Usage]:
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
    return llm.complete(role, system, user)


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
    "analyze",
    "bug_hunt",
    "classify",
    "failure_report",
    "generate",
    "hunt_giveup",
    "package",
    "parse_spec",
    "plan",
    "reflect",
    "route_after_hunt",
    "route_after_validate",
    "validate",
    "write_tests",
]
