"""The change ledger (who did what, to which files, and why) and the downloadable zip."""

from __future__ import annotations

import difflib
import shutil
import zipfile
from typing import Any

from .config import settings
from .llm import Usage


def entry(
    agent: str,
    action: str,
    rationale: str,
    *,
    files: list[str] | None = None,
    tokens: int = 0,
    iteration: int | None = None,
    model: str | None = None,
    diff: dict[str, str] | None = None,
) -> dict[str, Any]:
    """One ledger line. Every node returns at least one, which makes a run explainable afterwards."""
    e: dict[str, Any] = {"agent": agent, "action": action, "rationale": rationale}
    if files:
        e["files"] = files
    if tokens:
        e["tokens"] = tokens
    if iteration is not None:
        e["iteration"] = iteration
    if model:
        e["model"] = model
    if diff:
        e["diff"] = {p: d for p, d in diff.items() if d}
    return e


def model_names(usage: Usage) -> str | None:
    """The model(s) that actually served the call(s): shows fallbacks in the UI and ledger."""
    return ", ".join(usage.models) or None


def diff(path: str, before: str, after: str, max_lines: int = 60) -> str:
    lines = list(
        difflib.unified_diff(before.splitlines(), after.splitlines(), f"a/{path}", f"b/{path}", n=2, lineterm="")
    )
    if len(lines) > max_lines:
        lines = lines[:max_lines] + [f"... ({len(lines) - max_lines} more lines)"]
    return "\n".join(lines)


def ledger_markdown(state: dict[str, Any]) -> str:
    lines = ["# VibeStack change ledger", "", "| # | agent | action | rationale | files |", "|---|---|---|---|---|"]
    for i, e in enumerate(state.get("ledger", []), 1):
        files = ", ".join(e.get("files", []) or [])
        lines.append(f"| {i} | {e.get('agent')} | {e.get('action')} | {e.get('rationale', '')} | {files} |")
    bugs = state.get("bugs") or []
    if bugs:
        lines += ["", "## Bugs found by the agents", "", "| found by | problem | status |", "|---|---|---|"]
        for b in bugs:
            status = "fixed" if b.get("fixed") else "open"
            lines.append(f"| {b['found_by']} | {b['detail']} | {status} |")
    notes = state.get("review") or []
    if notes:
        lines += ["", "## Review notes", ""]
        lines += [f"- **{n['severity']}** `{n['path']}:{n['line']}` [{n['rule']}] {n['message']}" for n in notes]
    return "\n".join(lines) + "\n"


def write_artifact(state: dict[str, Any], project_name: str, extra: dict[str, str]) -> str:
    """Write the project to the run folder and zip it. Returns the zip path."""
    run_dir = settings.runs_dir / state["run_id"]
    project_dir = run_dir / "project"
    if project_dir.exists():
        shutil.rmtree(project_dir)
    files = {**state["files"], **extra}
    for rel, content in files.items():
        target = project_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    zip_path = run_dir / f"{project_name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, content in sorted(files.items()):
            zf.writestr(f"{project_name}/{rel}", content)
    return str(zip_path)
