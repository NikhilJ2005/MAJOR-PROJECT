"""LangGraph nodes. Each node is a pure-ish function: state in, partial state out.

Every node appends to the change ledger (who did what, to which files, and why),
which is what makes the run explainable after the fact.
"""

from __future__ import annotations

import logging
import re
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from langgraph.types import interrupt
from pydantic import BaseModel, Field, field_validator

from . import prompts
from .codegen import inject_fault, plan_files, render_project
from .config import settings
from .healing import classify_error, locate_failing_file, related_files
from .llm import LLMError, Usage, check_budget, get_llm, parse_file_blocks, structured
from .sandbox import get_sandbox
from .state import AgentState, EntitySpec, ProjectSpec

log = logging.getLogger(__name__)


def _spec(state: AgentState) -> ProjectSpec:
    return ProjectSpec.model_validate(state["spec"])


def _opts(state: AgentState) -> dict[str, Any]:
    return dict(state.get("options") or {})


def _spec_json(spec: ProjectSpec) -> str:
    return spec.model_dump_json(indent=1)


# --------------------------------------------------------------------------- spec


def parse_spec(state: AgentState) -> dict[str, Any]:
    if state.get("spec"):
        # Spec supplied up front (offline evals, or a re-run with an edited spec).
        spec = ProjectSpec.model_validate(state["spec"])
        return {
            "spec": spec.model_dump(),
            "status": "spec_ready",
            "ledger": [_entry("spec_architect", "used provided spec", "spec supplied with the request")],
        }
    llm = get_llm()
    if llm is None:
        raise LLMError("OPENROUTER_API_KEY is not set and no spec was provided")
    spec, usage = structured(
        llm, "strong", prompts.SPEC_SYSTEM, prompts.SPEC_USER.format(prompt=state["prompt"]), ProjectSpec
    )
    names = ", ".join(e.name for e in spec.entities)
    return {
        "spec": spec.model_dump(),
        "status": "spec_ready",
        "usage": usage.as_state(),
        "ledger": [
            _entry(
                "spec_architect",
                f"designed {len(spec.entities)} entities: {names}" + (" + JWT auth" if spec.auth else ""),
                "natural language converted to a validated ProjectSpec (single source of truth)",
                tokens=usage.total_tokens,
            )
        ],
    }


def approve_spec(state: AgentState) -> dict[str, Any]:
    if _opts(state).get("auto_approve"):
        return {"status": "approved"}
    decision = interrupt({"type": "approve_spec", "spec": state["spec"]})
    if not decision or not decision.get("approved", False):
        return {
            "status": "rejected",
            "ledger": [_entry("human", "rejected the spec", decision.get("reason", "") if decision else "")],
        }
    edited = decision.get("spec")
    if edited and edited != state["spec"]:
        spec = ProjectSpec.model_validate(edited)
        return {
            "spec": spec.model_dump(),
            "status": "approved",
            "ledger": [_entry("human", "approved an edited spec", "human-in-the-loop correction")],
        }
    return {"status": "approved", "ledger": [_entry("human", "approved the spec", "")]}


def route_after_approval(state: AgentState) -> str:
    return "plan" if state.get("status") == "approved" else "end"


# --------------------------------------------------------------------------- plan + generate


def plan(state: AgentState) -> dict[str, Any]:
    spec = _spec(state)
    file_plan = plan_files(spec)
    template_files = render_project(spec)
    return {
        "file_plan": file_plan,
        "template_files": template_files,
        "status": "planned",
        "ledger": [
            _entry(
                "planner",
                f"planned {len(file_plan)} files in dependency order",
                "models -> schemas -> routers -> app wiring -> tests -> packaging",
            )
        ],
    }


def generate(state: AgentState) -> dict[str, Any]:
    spec = _spec(state)
    opts = _opts(state)
    files = dict(state["template_files"])
    ledger: list[dict[str, Any]] = []
    total = Usage()
    mode = opts.get("codegen_mode") or settings.codegen_mode
    llm = get_llm() if mode == "llm" else None

    if llm is None:
        ledger.append(
            _entry("api_engineer", f"rendered {len(files)} files from templates", "deterministic codegen", files=sorted(files))
        )
    else:
        def work(e: EntitySpec) -> tuple[EntitySpec, dict[str, str], str, Usage]:
            return (e, *_generate_entity(llm, state["prompt"], spec, e, files))

        with ThreadPoolExecutor(max_workers=4) as pool:
            for e, new_files, rationale, usage in pool.map(work, spec.entities):
                total.add(usage)
                files.update(new_files)
                ledger.append(
                    _entry(
                        "api_engineer",
                        f"wrote {e.name} model/schema/router" if new_files else f"kept template for {e.name}",
                        rationale,
                        files=sorted(new_files),
                        tokens=usage.total_tokens,
                    )
                )

    if opts.get("inject_fault"):
        fault = inject_fault(spec, files)
        if fault:
            path, broken = fault
            files[path] = broken
            ledger.append(
                _entry("fault_injector", "planted a broken import (demo mode)", "proves the self-healing loop", files=[path])
            )

    out: dict[str, Any] = {"files": files, "status": "generated", "iteration": 0, "ledger": ledger}
    if total.calls:
        out["usage"] = total.as_state()
    return out


def _generate_entity(
    llm: Any, prompt: str, spec: ProjectSpec, e: EntitySpec, baseline: dict[str, str]
) -> tuple[dict[str, str], str, Usage]:
    expected = [f"app/models/{e.module}.py", f"app/schemas/{e.module}.py", f"app/routers/{e.module}.py"]
    reference = "\n\n".join(f"### FILE: {p}\n```python\n{baseline[p]}```" for p in expected)
    system = prompts.ENTITY_SYSTEM.format(module=e.module, name=e.name, table=e.table)
    user = prompts.ENTITY_USER.format(prompt=prompt, spec=_spec_json(spec), name=e.name, reference=reference)
    try:
        text, usage = llm.complete("strong", system, user)
    except LLMError as exc:
        return {}, f"LLM unavailable ({exc}); template kept", Usage()
    blocks = parse_file_blocks(text)
    new_files = {p: blocks[p] for p in expected if p in blocks and blocks[p].strip()}
    m = re.search(r"RATIONALE:\s*(.+)", text)
    return new_files, (m.group(1).strip() if m else "entity files generated"), usage


# --------------------------------------------------------------------------- review council


_STATIC_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("high", "dynamic code execution", re.compile(r"\b(eval|exec)\(|os\.system\(|subprocess\.")),
    ("high", "raw SQL built with string formatting", re.compile(r"text\(\s*f[\"']|execute\(\s*f[\"']")),
    ("medium", "password hash may be exposed in a response model", re.compile(r"class \w+Read\b[^\n]*\n(?:[^\n]*\n){0,8}?\s+password_hash")),
    ("medium", "CORS allows every origin", re.compile(r"allow_origins=\[\s*[\"']\*[\"']")),
]


def review(state: AgentState) -> dict[str, Any]:
    spec = _spec(state)
    files = state["files"]
    findings: list[dict[str, str]] = []
    for path, src in files.items():
        if not path.endswith(".py") or path.startswith("tests/"):
            continue
        for severity, issue, pattern in _STATIC_RULES:
            if pattern.search(src):
                findings.append({"severity": severity, "file": path, "issue": issue, "source": "static"})
        if path.startswith("app/routers/") and path not in ("app/routers/auth.py", "app/routers/__init__.py"):
            if "limit" not in src:
                findings.append({"severity": "medium", "file": path, "issue": "list endpoint has no limit", "source": "static"})
            if spec.auth and "get_current_user" not in src:
                findings.append({"severity": "high", "file": path, "issue": "write endpoints are not authenticated", "source": "static"})
    if spec.auth:
        findings.append(
            {"severity": "info", "file": "app/security.py", "issue": "set SECRET_KEY via environment in production", "source": "static"}
        )

    out: dict[str, Any] = {}
    llm = get_llm()
    if llm is not None:
        try:
            check_budget(state.get("usage"))
            routers = {p: s for p, s in files.items() if p.startswith(("app/routers/", "app/schemas/"))}
            blob = "\n\n".join(f"# {p}\n{s}" for p, s in routers.items())
            result, usage = structured(
                llm, "cheap", prompts.REVIEW_SYSTEM, prompts.REVIEW_USER.format(spec=_spec_json(spec), files=blob[:24000]), _ReviewOut
            )
            findings += [{**f.model_dump(), "source": "llm"} for f in result.findings]
            out["usage"] = usage.as_state()
        except LLMError as exc:
            log.warning("LLM review skipped: %s", exc)

    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ("high", "medium", "low", "info")}
    summary = ", ".join(f"{n} {s}" for s, n in counts.items() if n) or "no findings"
    out.update(
        {
            "review": findings,
            "status": "reviewed",
            "ledger": [_entry("review_council", f"reviewed code: {summary}", "advisory security/architecture pass")],
        }
    )
    return out


class _Finding(BaseModel):
    severity: str = "info"
    file: str = ""
    issue: str

    @field_validator("severity")
    @classmethod
    def _norm(cls, v: str) -> str:
        v = v.lower().strip()
        return v if v in ("high", "medium", "low", "info") else "info"


class _ReviewOut(BaseModel):
    findings: list[_Finding] = Field(default_factory=list)


# --------------------------------------------------------------------------- validate + heal


def validate(state: AgentState) -> dict[str, Any]:
    result = get_sandbox().validate(state["files"])
    it = state.get("iteration", 0)
    action = (
        f"all gates passed in {result.duration_s:.1f}s"
        if result.ok
        else f"failed at gate '{result.gate}' after {result.duration_s:.1f}s"
    )
    return {
        "validation": result.as_state(),
        "status": "validated" if result.ok else "validation_failed",
        "ledger": [_entry("validator", action, "import -> boot -> smoke tests", iteration=it)],
    }


def route_after_validate(state: AgentState) -> str:
    if state["validation"]["ok"]:
        return "package"
    if state.get("iteration", 0) >= settings.max_heal_iterations:
        return "failure_report"
    return "classify"


def classify(state: AgentState) -> dict[str, Any]:
    spec = _spec(state)
    v = state["validation"]
    error_class = classify_error(v["log"])
    path = locate_failing_file(v["log"], state["files"], spec)
    validation = {**v, "failing_file": path}
    return {
        "error_class": error_class,
        "validation": validation,
        "status": "classified",
        "ledger": [
            _entry(
                "error_classifier",
                f"classified as '{error_class}'" + (f" in {path}" if path else ""),
                prompts.ERROR_HINTS[error_class],
                files=[path] if path else [],
                iteration=state.get("iteration", 0),
            )
        ],
    }


def reflect(state: AgentState) -> dict[str, Any]:
    """Repair with escalating cost and certainty.

    attempt 1 .. N-2: cheap model     (most failures are small: imports, typos)
    attempt N-1:      strong model    (harder reasoning about the failure)
    attempt N:        verified templates for the failing entity (deterministic,
                      trades the LLM's improvements for a known-good build)
    Without an LLM every attempt uses the deterministic path.
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
    last_attempt = attempt >= settings.max_heal_iterations
    if llm is not None and path and not last_attempt:
        role = "strong" if attempt >= settings.max_heal_iterations - 1 else "cheap"
        try:
            check_budget(state.get("usage"))
            changed, diagnosis, usage = _llm_repair(llm, role, state, path, attempt)
            agent = f"reflector ({role})"
        except LLMError as exc:
            diagnosis = f"LLM repair unavailable ({exc})"

    if not changed and path:
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
            _entry(
                agent,
                f"attempt {attempt}: patched {', '.join(changed) or 'nothing'}",
                diagnosis,
                files=sorted(changed),
                tokens=usage.total_tokens,
                iteration=attempt,
            )
        ],
    }
    if usage.calls:
        out["usage"] = usage.as_state()
    return out


def _llm_repair(llm: Any, role: str, state: AgentState, path: str, attempt: int) -> tuple[dict[str, str], str, Usage]:
    v = state["validation"]
    files = state["files"]
    error_class = state.get("error_class", "unknown")
    related = "\n\n".join(f"# {p}\n```python\n{files[p]}```" for p in related_files(path, files))
    user = prompts.REFLECT_USER.format(
        gate=v["gate"],
        error_class=error_class,
        hint=prompts.ERROR_HINTS[error_class],
        attempt=attempt,
        max_attempts=settings.max_heal_iterations,
        log=v["log"][-4000:],
        path=path,
        content=files[path],
        related=related or "(none)",
    )
    text, usage = llm.complete(role, prompts.REFLECT_SYSTEM, user)
    changed = {
        p: body
        for p, body in parse_file_blocks(text).items()
        if p in files and not p.startswith("tests/") and body != files[p]
    }
    m = re.search(r"DIAGNOSIS:\s*(.+)", text)
    return changed, (m.group(1).strip() if m else ""), usage


def _template_restore(path: str, files: dict[str, str], templates: dict[str, str]) -> dict[str, str]:
    """Restore the failing file and its entity siblings, so model/schema/router stay consistent."""
    candidates = [path, *related_files(path, files)]
    return {p: templates[p] for p in candidates if p in templates and templates[p] != files.get(p)}


# --------------------------------------------------------------------------- outputs


def _write_artifact(state: AgentState, extra: dict[str, str]) -> str:
    run_dir = settings.runs_dir / state["run_id"]
    project_dir = run_dir / "project"
    if project_dir.exists():
        shutil.rmtree(project_dir)
    files = {**state["files"], **extra}
    for rel, content in files.items():
        target = project_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    name = _spec(state).project_name
    zip_path = run_dir / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in sorted(files.items()):
            zf.writestr(f"{name}/{rel}", content)
    return str(zip_path)


def _ledger_markdown(state: AgentState) -> str:
    lines = ["# VibeStack change ledger", "", "| # | agent | action | rationale | files |", "|---|---|---|---|---|"]
    for i, e in enumerate(state.get("ledger", []), 1):
        files = ", ".join(e.get("files", []) or [])
        lines.append(f"| {i} | {e.get('agent')} | {e.get('action')} | {e.get('rationale', '')} | {files} |")
    findings = state.get("review") or []
    if findings:
        lines += ["", "## Review findings", ""]
        lines += [f"- **{f['severity']}** `{f['file']}`: {f['issue']} ({f.get('source')})" for f in findings]
    return "\n".join(lines) + "\n"


def package(state: AgentState) -> dict[str, Any]:
    entry = _entry("devops_packager", "packaged project + ledger as zip", "validated output, ready to run", iteration=state.get("iteration", 0))
    final = {**state, "ledger": [*state.get("ledger", []), entry]}
    path = _write_artifact(final, {"VIBESTACK_LEDGER.md": _ledger_markdown(final)})
    return {"artifact_path": path, "status": "succeeded", "ledger": [entry]}


def failure_report(state: AgentState) -> dict[str, Any]:
    v = state["validation"]
    report = (
        f"# Generation failed validation\n\n"
        f"Circuit breaker tripped after {state.get('iteration', 0)} healing attempts.\n\n"
        f"- Failed gate: `{v['gate']}`\n- Error class: `{state.get('error_class', 'unknown')}`\n"
        f"- Suspected file: `{v.get('failing_file')}`\n\n## Last log\n\n```\n{v['log'][-3000:]}\n```\n"
    )
    entry = _entry("circuit_breaker", "stopped healing; produced diagnostic report", f"max {settings.max_heal_iterations} attempts reached")
    final = {**state, "ledger": [*state.get("ledger", []), entry]}
    path = _write_artifact(final, {"VIBESTACK_LEDGER.md": _ledger_markdown(final), "VIBESTACK_FAILURE.md": report})
    return {"artifact_path": path, "report": report, "status": "failed", "ledger": [entry]}


def _entry(agent: str, action: str, rationale: str, *, files: list[str] | None = None, tokens: int = 0, iteration: int | None = None) -> dict[str, Any]:
    e: dict[str, Any] = {"agent": agent, "action": action, "rationale": rationale}
    if files:
        e["files"] = files
    if tokens:
        e["tokens"] = tokens
    if iteration is not None:
        e["iteration"] = iteration
    return e


__all__ = [
    "parse_spec",
    "approve_spec",
    "plan",
    "generate",
    "review",
    "validate",
    "classify",
    "reflect",
    "package",
    "failure_report",
    "route_after_approval",
    "route_after_validate",
]
