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
ActionKind = Literal["custom", "transition", "adjust", "move"]

# Names that clash with columns we add ourselves or with ORM internals (SQLAlchemy, Django).
RESERVED_FIELD_NAMES = {"id", "created_at", "metadata", "registry", "query", "objects", "pk", "save", "delete", "clean"}
ACTION_PATH = re.compile(r"^(/\{item_id\})?/[a-z][a-z0-9_-]*$")

# Production features the verified templates provide when the design turns them on (they need logins).
FEATURES = {
    "refresh_tokens": "short-lived access tokens plus rotating refresh tokens; reusing an old one logs out its family",
    "account_lockout": "lock an account for 15 minutes after 5 failed logins (423)",
    "audit_log": "an immutable audit_logs table of every change, readable by admins at GET /audit_logs/",
    "rate_limit": "token-bucket rate limits on login, sign-up and custom actions (429); Redis when REDIS_URL is set",
}
# Role names that mean "super admin": those users come from ADMIN_EMAILS, not from the role list.
ADMIN_ROLE_NAMES = {"admin", "super_admin", "superadmin", "administrator", "super_administrator"}
# Tables the feature templates own; an entity with the same name would clash with them.
FEATURE_ENTITIES = {"refresh_tokens": "RefreshToken", "audit_log": "AuditLog"}


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
    write_roles: list[str] = Field(
        default_factory=list, description="Only users with one of these roles (or admins) may create, change or delete"
    )
    sample_rows: list[dict[str, Any]] = Field(
        default_factory=list,
        description="5-8 realistic demo rows the app starts with (field name -> value). A foreign key holds the "
        "1-based position of the row in the target entity's sample_rows; dates are ISO strings",
    )

    @field_validator("name")
    @classmethod
    def _pascal(cls, v: str) -> str:
        return to_pascal(v)

    @field_validator("sample_rows", mode="before")
    @classmethod
    def _rows_only(cls, v: Any) -> list[dict[str, Any]]:
        """A malformed demo row from the model is dropped instead of failing the whole design."""
        return [r for r in v if isinstance(r, dict)] if isinstance(v, list) else []

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
    roles: list[str] = Field(default_factory=list, description="Only users with one of these roles (or admins) may call it")
    locks: bool = Field(default=False, description="Moves money or stock: run it in a transaction with the row locked")
    requirement_ids: list[str] = Field(default_factory=list)
    inputs: list[FieldSpec] = Field(
        default_factory=list,
        description="The JSON body (POST) or query (GET) the action takes, e.g. [{name: amount, type: decimal, min: 0.01}]; "
        "checked before the action runs (422). Empty when it needs no input",
    )
    kind: ActionKind = Field(
        default="custom",
        description="transition: set `field` (a choices field) to `target`; adjust: add (direction add) or subtract "
        "(direction subtract) the input amount on numeric `field`; move: move the input amount from numeric `field` to "
        "numeric `target` of the same row; custom: anything else (written by the Code Generator)",
    )
    field: str | None = Field(default=None, description="transition/adjust/move: the field it changes")
    target: str | None = Field(default=None, description="transition: the new state; move: the field that receives")
    direction: Literal["add", "subtract"] = "add"

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


class PluginChoice(BaseModel):
    name: str = Field(description="A plugin name from the catalog")
    reason: str = Field(default="", description="Why this app needs it, in one short sentence")

    @field_validator("name")
    @classmethod
    def _snake(cls, v: str) -> str:
        return to_snake(v)


class RoleSpec(BaseModel):
    """A kind of user, e.g. client or freelancer. Admins (ADMIN_EMAILS) are always there and may do everything."""

    name: str = Field(description="snake_case, e.g. client")
    self_signup: bool = Field(default=True, description="Users may pick it at sign-up; otherwise an admin assigns it")
    description: str = ""

    @field_validator("name")
    @classmethod
    def _snake(cls, v: str) -> str:
        return to_snake(v)


class ProjectSpec(BaseModel):
    project_name: str
    description: str = ""
    stack: StackName = "fastapi"
    entities: list[EntitySpec]
    auth: bool = Field(default=False, description="Add JWT register/login/me endpoints")
    features: list[str] = Field(
        default_factory=list, description="Production features: " + ", ".join(FEATURES) + " (others are kept as notes)"
    )
    roles: list[RoleSpec] = Field(default_factory=list, description="User roles; the first self-signup role is the default")
    plugins: list[PluginChoice] = Field(default_factory=list, description="Plugins from the catalog this app needs")
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
        self.features = list(dict.fromkeys(to_snake(f) if to_snake(f) in FEATURES else f for f in self.features))
        if not self.auth:  # every production feature but rate limits is about logged-in users
            self.features = [f for f in self.features if f not in FEATURES or f == "rate_limit"]
        clash = {FEATURE_ENTITIES[f] for f in self.features if f in FEATURE_ENTITIES}
        self.entities = [e for e in self.entities if e.name not in clash]
        seen = {e.name: e for e in self.entities}
        if not self.entities:
            raise ValueError("spec must contain at least one entity of its own")
        known = set(seen) | ({"User"} if self.auth else set())
        for ent in self.entities:
            ent.fields = _clean_fields(ent, known)
        self.entities = topo_sort(self.entities)
        self.actions = _clean_actions(self.actions, seen)
        _clean_roles(self)
        for ent in self.entities:
            ent.sample_rows = clean_samples(ent, seen)
        return self

    def has(self, feature: str) -> bool:
        return feature in self.features

    @property
    def role_names(self) -> list[str]:
        return [r.name for r in self.roles]

    @property
    def default_role(self) -> str | None:
        return next((r.name for r in self.roles if r.self_signup), None)

    def entity(self, name: str) -> EntitySpec:
        return next(e for e in self.entities if e.name == name)

    def actions_for(self, e: EntitySpec) -> list[ActionSpec]:
        return [a for a in self.actions if a.entity == e.name]

    def custom_actions_for(self, e: EntitySpec) -> list[ActionSpec]:
        """The actions the Code Generator writes (pattern actions are verified template code)."""
        return [a for a in self.actions if a.entity == e.name and a.kind == "custom"]


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


MAX_SAMPLE_ROWS = 10
FALLBACK_SAMPLE_ROWS = 5


def _sample_cell(f: FieldSpec, value: Any, row: int, targets: dict[str, int]) -> Any:
    """One demo value made valid for its field, or None when it cannot be."""
    if value is None:
        return None
    if f.references:
        size = targets.get(f.references, 1 if f.references == "User" else 0)
        try:
            pos = int(value)
        except (TypeError, ValueError):
            return None
        return pos if 1 <= pos <= size else (((row - 1) % size) + 1 if size else None)
    if f.choices:
        return value if value in f.choices else f.choices[0]
    try:
        if f.type == "bool":
            return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes")
        if f.type == "int":
            value = int(float(value))
        elif f.type in ("float", "decimal"):
            value = round(float(value), 2)
        elif f.type in ("date", "datetime"):
            import datetime as dt

            text = str(value).replace("Z", "+00:00")
            if f.type == "date":
                return dt.date.fromisoformat(text[:10]).isoformat()
            try:
                return dt.datetime.fromisoformat(text).isoformat()
            except ValueError:
                return dt.datetime.fromisoformat(text[:10]).isoformat()
        elif f.type == "json":
            return value if isinstance(value, (dict, list)) else None
        else:
            value = str(value)
            return value[:255] if f.type == "str" else value
    except (TypeError, ValueError):
        return None
    if isinstance(value, (int, float)):
        if f.min is not None and value < f.min:
            value = type(value)(f.min)
        if f.max is not None and value > f.max:
            value = type(value)(f.max)
    return value


def _fallback_cell(ent: EntitySpec, f: FieldSpec, row: int, targets: dict[str, int]) -> Any:
    """A deterministic demo value, so an entity is never empty even when the model gave no rows."""
    import datetime as dt

    if f.references:
        size = targets.get(f.references, 1 if f.references == "User" else 0)
        return ((row - 1) % size) + 1 if size else None
    if f.choices:
        return f.choices[(row - 1) % len(f.choices)] if not f.transitions else f.choices[0]
    words = f.name.replace("_", " ")
    if f.type == "str":
        if "email" in f.name:
            return f"{ent.module}{row}@example.com"
        return f"{ent.name} {words.title()} {row}" if f.name in ("name", "title") else f"{words.capitalize()} {row}"
    if f.type == "text":
        return f"Demo {words} for {ent.name.lower()} #{row}."
    if f.type == "bool":
        return row % 2 == 1
    if f.type in ("int", "float", "decimal"):
        low = f.min if f.min is not None else 1
        high = f.max if f.max is not None else low + 100
        value = low + (high - low) * ((row * 37) % 100) / 100
        return int(value) if f.type == "int" else round(value, 2)
    if f.type == "date":
        return (dt.date(2026, 1, 1) + dt.timedelta(days=7 * row)).isoformat()
    if f.type == "datetime":
        return (dt.datetime(2026, 1, 1, 9, 0, tzinfo=dt.UTC) + dt.timedelta(days=7 * row)).isoformat()
    if f.type == "json":
        return {}
    return None


def clean_samples(ent: EntitySpec, entities: dict[str, EntitySpec]) -> list[dict[str, Any]]:
    """Make the Architect's demo rows safe to insert: known fields only, valid values, foreign keys that
    point at a demo row of the target. Without usable rows, deterministic ones are made up instead."""
    targets = {name: min(max(len(e.sample_rows), FALLBACK_SAMPLE_ROWS), MAX_SAMPLE_ROWS) for name, e in entities.items()}
    rows: list[dict[str, Any]] = []
    for i, raw in enumerate(ent.sample_rows[:MAX_SAMPLE_ROWS], 1):
        if not isinstance(raw, dict):
            continue
        row = {}
        for f in ent.fields:
            value = _sample_cell(f, raw.get(f.name, raw.get(f.name[:-3]) if f.references else raw.get(f.name)), i, targets)
            if value is None and f.required:
                value = _fallback_cell(ent, f, i, targets)
            row[f.name] = value
        rows.append(row)
    if not rows:
        rows = [{f.name: _fallback_cell(ent, f, i, targets) for f in ent.fields}
                for i in range(1, FALLBACK_SAMPLE_ROWS + 1)]
    for f in ent.fields:  # unique values must differ from row to row
        if f.unique and not f.references and f.type in ("str", "text", "int"):
            seen: set[Any] = set()
            for i, row in enumerate(rows, 1):
                if row.get(f.name) in seen:
                    row[f.name] = (f"{row[f.name]} {i}" if f.type != "int" else (row[f.name] or 0) + 1000 + i)
                seen.add(row.get(f.name))
    return rows


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
        _clean_action_inputs(a, set(entities))
        _clean_pattern(a, entities[a.entity])
        out.append(a)
    return out


def _clean_action_inputs(a: ActionSpec, known: set[str]) -> None:
    """Inputs are checked like fields; unknown references and duplicates are dropped."""
    names: set[str] = set()
    kept: list[FieldSpec] = []
    for f in a.inputs:
        if f.references:
            target = to_pascal(f.references)
            if target not in known and target != "User":
                continue
            f.references, f.type, f.choices = target, "int", []
        if f.choices:
            f.type = "str"
        f.transitions, f.on_delete, f.unique, f.index = {}, None, False, False
        if f.name in names or f.name in RESERVED_FIELD_NAMES:
            continue
        names.add(f.name)
        kept.append(f)
    a.inputs = kept


def _clean_pattern(a: ActionSpec, ent: EntitySpec) -> None:
    """A pattern action the verified templates can run, or "custom" (the Code Generator writes it)."""
    fields = {f.name: f for f in ent.fields}
    number = lambda name: name in fields and fields[name].is_number
    ok = a.on_item and a.method == "POST"
    if a.kind == "transition":
        f = fields.get(a.field or "")
        ok = ok and f is not None and bool(f.choices) and a.target in f.choices
    elif a.kind == "adjust":
        ok = ok and number(a.field)
    elif a.kind == "move":
        ok = ok and number(a.field) and number(a.target) and a.field != a.target
    if not ok:
        a.kind = "custom"
    if a.kind in ("adjust", "move"):
        source = fields[a.field]
        amount = next((i for i in a.inputs if i.name == "amount"), None)
        if amount is None:
            amount = FieldSpec(name="amount", type="int" if source.type == "int" else source.type)
            a.inputs.insert(0, amount)
        amount.type = "int" if source.type == "int" else source.type
        amount.required = True
        if amount.min is None or amount.min <= 0:
            amount.min = 1 if amount.type == "int" else 0.01
        a.locks = True  # money and stock: the row stays locked while it changes


def _clean_roles(spec: ProjectSpec) -> None:
    """Roles need logins. "admin" means the ADMIN_EMAILS users; unknown role names are dropped."""
    if not spec.auth:
        spec.roles = []
    roles: dict[str, RoleSpec] = {}
    for r in spec.roles:
        if r.name not in ADMIN_ROLE_NAMES and r.name not in roles:
            roles[r.name] = r
    spec.roles = list(roles.values())
    if spec.roles and not any(r.self_signup for r in spec.roles):
        spec.roles[0].self_signup = True  # somebody must be able to sign up

    def clean(names: list[str]) -> tuple[list[str], bool]:
        """(the known roles in names, whether only admins are left)"""
        snake = [to_snake(n) for n in names]
        kept = list(dict.fromkeys(n for n in snake if n in roles))
        return kept, bool(snake) and not kept and any(n in ADMIN_ROLE_NAMES for n in snake)

    for e in spec.entities:
        e.write_roles, admins_only = clean(e.write_roles)
        if admins_only:
            e.write_access = "admin"
        if e.write_access == "admin" or set(e.write_roles) == set(roles):
            e.write_roles = []  # admins only, or everyone: nothing to check
    for a in spec.actions:
        a.roles, admins_only = clean(a.roles)
        a.admin_only = a.admin_only or admins_only
        if a.admin_only or set(a.roles) == set(roles):
            a.roles = []


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
    plugins: list[str]  # plugins the user forces on (the agents choose the rest, see plugins.resolve)
    plugins_off: list[str]  # plugins the user forces off
    auto_plugins: bool  # let the Architect and Planner choose plugins (default True)
    # Ablation study switches (evals/run.py --ablation); normal runs leave them unset.
    tools: bool  # the Reflector may call tools (default True)
    edge_tests: bool  # include the spec-derived edge-case tests (default True)
    fixed_repairs: int  # stop after exactly N repairs instead of the progress-based rule
    rule_tests: bool  # use the Architect's rule tests during validation (default True)
    calibrate: bool  # check the rule tests against the verified templates before any code (default True)
    resume: bool  # "keep fixing": validate and heal the given files, skipping design and code generation
    instructions: str  # project rules from the user: every agent follows them
    review_design: bool  # wait for the user to approve (or edit) the design before any code is written


class AgentState(TypedDict, total=False):
    run_id: str
    prompt: str
    options: Options
    base_run_id: str  # follow-up edits: the version this run starts from
    base_spec: dict[str, Any]
    base_files: dict[str, str]
    rule_tests_wanted: bool  # the Architect designed this run, so it also writes rule tests
    held_out: dict[str, str]  # ablation study: rule tests kept out of validation, run afterwards
    base_failed: bool  # the version being edited never passed validation: none of its code is reused
    changes: dict[str, list[str]]  # follow-up edits: entities added / changed / removed by the Architect
    spec: dict[str, Any]
    file_plan: list[dict[str, str]]
    design_feedback: str | None  # the user asked for a design change in words: back to the Architect
    plugin_choices: list[dict[str, str]]  # the plugins this build uses: {name, by, reason} (plugins.resolve)
    files: Annotated[dict[str, str], merge_files]
    template_files: dict[str, str]
    validation: Validation
    iteration: int
    best_files: dict[str, str]  # the version with the best validation so far (rolled back to after a bad repair)
    best_validation: dict[str, Any]
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
