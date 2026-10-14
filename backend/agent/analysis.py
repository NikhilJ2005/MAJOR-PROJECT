"""Requirements: the Analyst's numbered list, and the check that the design covers all of it."""

from __future__ import annotations

from pydantic import BaseModel

from .state import ProjectSpec, Requirement


class RequirementList(BaseModel):
    requirements: list[Requirement]


def check_coverage(spec: ProjectSpec) -> list[str]:
    """Requirement ids that no entity or action claims to cover (empty list = fully covered)."""
    covered = {rid for e in spec.entities for rid in e.requirement_ids}
    covered |= {rid for a in spec.actions for rid in a.requirement_ids}
    return [r.id for r in spec.requirements if r.id not in covered]


def requirements_text(requirements: list[Requirement] | list[dict]) -> str:
    """The list as plain text for prompts and the README: `R1 (rule): ...`."""
    rows = [Requirement.model_validate(r) for r in requirements]
    return "\n".join(f"{r.id} ({r.kind}): {r.text}" for r in rows) or "(none: build exactly what the description says)"
