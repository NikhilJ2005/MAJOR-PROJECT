"""Graph state and the ProjectSpec intermediate representation.

The spec is the single source of truth: every downstream node (planner,
generator, smoke-test writer, reviewer) reads it instead of the raw prompt.
"""

from __future__ import annotations

import keyword
import operator
import re
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field, field_validator, model_validator

FieldType = Literal["str", "text", "int", "float", "bool", "datetime"]

RESERVED_FIELD_NAMES = {"id", "created_at", "metadata", "registry", "query"}


def to_snake(name: str) -> str:
    name = re.sub(r"[^0-9a-zA-Z]+", "_", name.strip())
    name = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", name)
    name = re.sub(r"_+", "_", name).strip("_").lower()
    if not name or name[0].isdigit():
        name = f"f_{name}"
    if keyword.iskeyword(name):
        name = f"{name}_"
    return name


def to_pascal(name: str) -> str:
    return "".join(part.capitalize() for part in to_snake(name).split("_")) or "Item"


def pluralize(word: str) -> str:
    if word.endswith("y") and not word.endswith(("ay", "ey", "oy", "uy")):
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "z", "ch", "sh")):
        return word + "es"
    return word + "s"


class FieldSpec(BaseModel):
    name: str
    type: FieldType = "str"
    required: bool = True
    unique: bool = False
    references: str | None = Field(
        default=None, description="Entity name this field is a foreign key to (type must be int)"
    )

    @field_validator("name")
    @classmethod
    def _snake(cls, v: str) -> str:
        return to_snake(v)


class EntitySpec(BaseModel):
    name: str = Field(description="PascalCase singular, e.g. BlogPost")
    fields: list[FieldSpec] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _pascal(cls, v: str) -> str:
        return to_pascal(v)

    @property
    def module(self) -> str:
        return to_snake(self.name)

    @property
    def table(self) -> str:
        return pluralize(self.module)

    @property
    def foreign_keys(self) -> list[FieldSpec]:
        return [f for f in self.fields if f.references]


class ProjectSpec(BaseModel):
    project_name: str
    description: str = ""
    entities: list[EntitySpec]
    auth: bool = Field(default=False, description="Add JWT register/login/me endpoints")
    features: list[str] = Field(default_factory=list, description="e.g. pagination, search")

    @field_validator("project_name")
    @classmethod
    def _project(cls, v: str) -> str:
        return to_snake(v)

    @model_validator(mode="after")
    def _normalize(self) -> "ProjectSpec":
        if not self.entities:
            raise ValueError("spec must contain at least one entity")
        if self.auth:
            # Auth owns the users table; a separate User CRUD entity would clash with it.
            self.entities = [e for e in self.entities if e.name != "User"]
            if not self.entities:
                raise ValueError("spec must contain at least one entity besides User")
        seen: dict[str, EntitySpec] = {}
        for ent in self.entities:
            if ent.name in seen:
                raise ValueError(f"duplicate entity {ent.name}")
            seen[ent.name] = ent
        known = set(seen) | ({"User"} if self.auth else set())
        for ent in self.entities:
            names: set[str] = set()
            cleaned: list[FieldSpec] = []
            for f in ent.fields:
                if f.name in RESERVED_FIELD_NAMES or f.name in names:
                    continue
                if f.references:
                    target = to_pascal(f.references)
                    if target not in known:
                        raise ValueError(f"{ent.name}.{f.name} references unknown entity {target}")
                    f.references = target
                    f.type = "int"
                names.add(f.name)
                cleaned.append(f)
            ent.fields = cleaned
        self.entities = topo_sort(self.entities)
        return self


def topo_sort(entities: list[EntitySpec]) -> list[EntitySpec]:
    """Order entities so that FK targets come first (needed for smoke-test data)."""
    by_name = {e.name: e for e in entities}
    ordered: list[EntitySpec] = []
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(e: EntitySpec) -> None:
        if e.name in done:
            return
        if e.name in visiting:
            raise ValueError(f"circular foreign keys involving {e.name}")
        visiting.add(e.name)
        for fk in e.foreign_keys:
            if fk.references in by_name and fk.references != e.name:
                visit(by_name[fk.references])
        visiting.discard(e.name)
        done.add(e.name)
        ordered.append(e)

    for e in entities:
        visit(e)
    return ordered


class LedgerEntry(TypedDict, total=False):
    agent: str
    action: str
    rationale: str
    files: list[str]
    tokens: int
    iteration: int
    model: str
    diff: dict[str, str]


def merge_usage(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(a or {})
    for k, v in (b or {}).items():
        out[k] = round(out.get(k, 0) + v, 6) if isinstance(v, (int, float)) else v
    return out


def merge_files(a: dict[str, str] | None, b: dict[str, str] | None) -> dict[str, str]:
    return {**(a or {}), **(b or {})}


class Validation(TypedDict, total=False):
    ok: bool
    gate: str
    log: str
    failing_file: str | None
    duration_s: float


class Options(TypedDict, total=False):
    inject_fault: bool  # legacy flag, same as fault="import"
    fault: str  # "none" | "import" | "status"
    codegen_mode: str


class AgentState(TypedDict, total=False):
    run_id: str
    prompt: str
    options: Options
    spec: dict[str, Any]
    file_plan: list[dict[str, str]]
    files: Annotated[dict[str, str], merge_files]
    template_files: dict[str, str]
    validation: Validation
    iteration: int
    error_class: str
    ledger: Annotated[list[LedgerEntry], operator.add]
    usage: Annotated[dict[str, Any], merge_usage]
    status: str
    artifact_path: str
    report: str
