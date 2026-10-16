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

FieldType = Literal["str", "text", "int", "float", "decimal", "bool", "date", "datetime", "json"]
OnDelete = Literal["cascade", "restrict", "set_null"]
StackName = Literal["fastapi", "flask", "django"]
WriteAccess = Literal["any_user", "owner", "admin"]

# Names that clash with columns we add ourselves or with ORM internals (SQLAlchemy, Django).
RESERVED_FIELD_NAMES = {"id", "created_at", "metadata", "registry", "query", "objects", "pk", "save", "delete", "clean"}
ACTION_PATH = re.compile(r"^(/\{item_id\})?/[a-z][a-z0-9_-]*$")


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
    choices: list[str] = Field(default_factory=list, description="Allowed values, e.g. order status")
    min: float | None = Field(default=None, description="Smallest allowed number")
    max: float | None = Field(default=None, description="Largest allowed number")
    transitions: dict[str, list[str]] = Field(
        default_factory=dict, description='State machine on a choices field, e.g. {"draft": ["running"]}'
    )
    on_delete: OnDelete | None = Field(default=None, description="For foreign keys: cascade, restrict or set_null")
    index: bool = Field(default=False, description="Add a database index (foreign keys always get one)")

    @field_validator("name")
    @classmethod
    def _snake(cls, v: str) -> str:
        return to_snake(v)

    @property
    def is_number(self) -> bool:
        return self.type in ("int", "float", "decimal") and not self.references


class EntitySpec(BaseModel):
    name: str = Field(description="PascalCase singular, e.g. BlogPost")
    fields: list[FieldSpec] = Field(default_factory=list)
    write_access: WriteAccess = Field(
        default="any_user",
        description="any_user: any logged-in user may write; owner: rows are private to the user who "
        "created them (owner_id is added for you); admin: only admins may write, everyone may read",
    )
    requirement_ids: list[str] = Field(default_factory=list, description="Requirements this entity covers")
    realtime: bool = Field(default=False, description="Stream create/update/delete events live (Server-Sent Events)")

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

    @property
    def owned(self) -> bool:
        return self.write_access == "owner"


class ActionSpec(BaseModel):
    """A custom endpoint beyond CRUD, e.g. POST /orders/{item_id}/checkout."""

    name: str = Field(description="snake_case function name, e.g. checkout")
    entity: str = Field(description="Entity whose router owns this endpoint")
    method: Literal["GET", "POST"] = "POST"
    path: str = Field(description='"/{item_id}/<word>" for one row, or "/<word>" for the collection')
    description: str = ""
    rules: list[str] = Field(default_factory=list, description="Business rules in plain English, incl. error codes")
    admin_only: bool = False
    requirement_ids: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _snake(cls, v: str) -> str:
        return to_snake(v)

    @field_validator("entity")
    @classmethod
    def _pascal(cls, v: str) -> str:
        return to_pascal(v)

    @field_validator("path")
    @classmethod
    def _path(cls, v: str) -> str:
        v = "/" + v.strip().strip("/")
        v = re.sub(r"\{\w+\}", "{item_id}", v)
        if not ACTION_PATH.match(v):
            raise ValueError(f"action path {v!r} must look like /{{item_id}}/word or /word")
        return v

    @property
    def on_item(self) -> bool:
        return self.path.startswith("/{item_id}")

    @property
    def segment(self) -> str:
        return self.path.rsplit("/", 1)[-1]


class Requirement(BaseModel):
    id: str = Field(description="R1, R2, ...")
    text: str
    kind: Literal["data", "rule", "permission", "endpoint"] = "data"


class ProjectSpec(BaseModel):
    project_name: str
    description: str = ""
    stack: StackName = "fastapi"
    entities: list[EntitySpec]
    auth: bool = Field(default=False, description="Add JWT register/login/me endpoints")
    features: list[str] = Field(default_factory=list, description="e.g. pagination, search")
    requirements: list[Requirement] = Field(default_factory=list)
    actions: list[ActionSpec] = Field(default_factory=list)

    @field_validator("project_name")
    @classmethod
    def _project(cls, v: str) -> str:
        return to_snake(v)

    @model_validator(mode="after")
    def _normalize(self) -> ProjectSpec:
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
            if not self.auth:
                ent.write_access = "any_user"  # ownership and admins need logins
        known = set(seen) | ({"User"} if self.auth else set())
        for ent in self.entities:
            ent.fields = _clean_fields(ent, known)
        self.entities = topo_sort(self.entities)
        self.actions = _clean_actions(self.actions, seen)
        return self

    def entity(self, name: str) -> EntitySpec:
        return next(e for e in self.entities if e.name == name)

    def actions_for(self, e: EntitySpec) -> list[ActionSpec]:
        return [a for a in self.actions if a.entity == e.name]


def _clean_fields(ent: EntitySpec, known: set[str]) -> list[FieldSpec]:
    """Drop reserved/duplicate fields and make foreign keys and enums consistent."""
    names: set[str] = set()
    cleaned: list[FieldSpec] = []
    for f in ent.fields:
        if f.references:
            target = to_pascal(f.references)
            if target not in known:
                raise ValueError(f"{ent.name}.{f.name} references unknown entity {target}")
            f.references = target
            f.type = "int"
            f.choices = []
            if not f.name.endswith("_id"):
                f.name = f"{f.name}_id"  # every ORM expects <name>_id for the key column
        if f.choices:
            f.type = "str"
        _clean_rules(f)
        if f.name in RESERVED_FIELD_NAMES or f.name in names:
            continue
        if ent.owned and f.name == "owner_id":
            continue  # added automatically for owner-scoped entities
        names.add(f.name)
        cleaned.append(f)
    return cleaned


def _clean_rules(f: FieldSpec) -> None:
    """Keep only rules that make sense for the field, so every template can rely on them."""
    if f.references:
        if f.on_delete is None or (f.on_delete == "set_null" and f.required):
            f.on_delete = "restrict"  # never silently lose data, never null a required field
    else:
        f.on_delete = None
    known = set(f.choices)
    f.transitions = {
        state: [t for t in targets if t in known and t != state]
        for state, targets in f.transitions.items()
        if state in known
    }
    if f.transitions and f.choices[0] not in f.transitions:
        # Start new rows in a state that can move on: the first state with outgoing transitions.
        first = next((c for c in f.choices if f.transitions.get(c)), f.choices[0])
        f.choices = [first] + [c for c in f.choices if c != first]
    if f.type == "json":
        f.unique, f.index, f.min, f.max, f.choices = False, False, None, None, []


def _clean_actions(actions: list[ActionSpec], entities: dict[str, EntitySpec]) -> list[ActionSpec]:
    """Actions must belong to a known entity and have unique names and routes."""
    seen: set[str] = set()
    out: list[ActionSpec] = []
    for a in actions:
        if a.entity not in entities:
            raise ValueError(f"action {a.name} belongs to unknown entity {a.entity}")
        key = f"{a.entity}.{a.name}"
        route = f"{a.entity} {a.method} {a.path}"
        if key in seen or route in seen:
            continue
        seen |= {key, route}
        out.append(a)
    return out


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
    stack: str  # "fastapi" | "flask" | "django"
    codegen_mode: str  # "llm" | "template"
    plugins: list[str]  # plugin names from backend/plugins/ (default: the PLUGINS env var)
    # Ablation study switches (evals/run.py --ablation); normal runs leave them unset.
    tools: bool  # the Reflector may call tools (default True)
    edge_tests: bool  # include the spec-derived edge-case tests (default True)
    fixed_repairs: int  # stop after exactly N repairs instead of the progress-based rule


class AgentState(TypedDict, total=False):
    run_id: str
    prompt: str
    options: Options
    base_run_id: str  # follow-up edits: the version this run starts from
    base_spec: dict[str, Any]
    base_files: dict[str, str]
    changes: dict[str, list[str]]  # follow-up edits: entities added / changed / removed by the Architect
    spec: dict[str, Any]
    file_plan: list[dict[str, str]]
    files: Annotated[dict[str, str], merge_files]
    template_files: dict[str, str]
    validation: Validation
    iteration: int
    heal_history: list[dict[str, Any]]  # one entry per validation, read by healing.should_continue
    bugs: list[dict[str, Any]]  # every problem found during the run, and whether it was fixed
    review: list[dict[str, Any]]  # low-severity static-check notes
    stop_reason: str
    error_class: str
    ledger: Annotated[list[LedgerEntry], operator.add]
    usage: Annotated[dict[str, Any], merge_usage]
    status: str
    artifact_path: str
    report: str
