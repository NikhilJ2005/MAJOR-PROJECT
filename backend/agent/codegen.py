"""Deterministic half of code generation: file planning and template rendering.

Templates give every project a known-good skeleton for its framework (see
stacks.py). In LLM mode the model rewrites the per-entity files, but the
rendered templates are kept as the reference contract and as the last-resort
repair source for self-healing.
"""

from __future__ import annotations

import json
import pprint
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .stacks import Stack, get_stack
from .state import EntitySpec, FieldSpec, ProjectSpec, pluralize, to_snake

TEMPLATE_ROOT = Path(__file__).parent / "templates"

# --------------------------------------------------------------------------- field types per library

_PYDANTIC = {"str": "str", "text": "str", "int": "int", "float": "float", "decimal": "float",
             "bool": "bool", "date": "date", "datetime": "datetime"}
_SQLALCHEMY = {"str": "String(255)", "text": "Text", "int": "Integer", "float": "Float",
               "decimal": "Numeric(12, 2, asdecimal=False)", "bool": "Boolean", "date": "Date",
               "datetime": "DateTime(timezone=True)"}
_DJANGO = {"str": "CharField", "text": "TextField", "int": "IntegerField", "float": "FloatField",
           "decimal": "DecimalField", "bool": "BooleanField", "date": "DateField", "datetime": "DateTimeField"}
_SAMPLES: dict[str, Any] = {"int": 7, "float": 1.5, "decimal": 9.99, "bool": True,
                            "date": "2026-01-01", "datetime": "2026-01-01T00:00:00"}
_WRONG: dict[str, Any] = {"str": {"not": "text"}, "text": {"not": "text"}, "bool": "maybe"}


def pydantic_type(f: FieldSpec) -> str:
    if f.choices:
        return "Literal[" + ", ".join(json.dumps(c) for c in f.choices) + "]"
    return _PYDANTIC[f.type]


def constraints(f: FieldSpec) -> str:
    """Pydantic Field(...) arguments that enforce the spec's rules."""
    args = []
    if f.type == "str" and not f.choices:
        args.append("max_length=255")
    if f.is_number and f.min is not None:
        args.append(f"ge={f.min:g}")
    if f.is_number and f.max is not None:
        args.append(f"le={f.max:g}")
    return ", ".join(args)


def input_decl(f: FieldSpec, optional: bool) -> str:
    """Type and default of a field in a Create/Update schema."""
    t, args = pydantic_type(f), constraints(f)
    if optional:
        return f"{t} | None = Field(default=None, {args})" if args else f"{t} | None = None"
    return f"{t} = Field({args})" if args else t


def read_type(f: FieldSpec) -> str:
    return "str" if f.choices else _PYDANTIC[f.type]


def sa_py_type(f: FieldSpec) -> str:
    return {"date": "date", "datetime": "datetime", "bool": "bool", "int": "int",
            "float": "float", "decimal": "float"}.get(f.type, "str")


def django_field(f: FieldSpec) -> str:
    """The models.<Field>(...) expression for a Django model."""
    optional = [] if f.required else ["null=True", "blank=True"]
    if f.references:
        args = [f'"{f.references}"', "on_delete=models.CASCADE", 'related_name="+"', *optional]
        return f"models.ForeignKey({', '.join(args)})"
    args = {"str": ["max_length=255"], "decimal": ["max_digits=12", "decimal_places=2"]}.get(f.type, [])
    if f.unique:
        args.append("unique=True")
    return f"models.{_DJANGO[f.type]}({', '.join(args + optional)})"


def table_of(name: str) -> str:
    return pluralize(to_snake(name))


def search_field(e: EntitySpec) -> str | None:
    for f in e.fields:
        if f.type in ("str", "text") and not f.references and not f.choices:
            return f.name
    return None


def access_text(e: EntitySpec, auth: bool) -> str:
    if e.write_access == "owner":
        return "only the owner (rows are private to the user who created them)"
    if e.write_access == "admin":
        return "admins only (everyone can read)"
    return "any logged-in user (everyone can read)" if auth else "anyone"


# --------------------------------------------------------------------------- rendering

_envs: dict[str, Environment] = {}


def _env(stack: Stack) -> Environment:
    if stack.name not in _envs:
        env = Environment(
            loader=FileSystemLoader([TEMPLATE_ROOT / d for d in stack.template_dirs]),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
        env.globals.update(
            input_decl=input_decl, read_type=read_type, sa_type=lambda f: _SQLALCHEMY[f.type],
            sa_py_type=sa_py_type, django_field=django_field, table_of=table_of,
        )
        _envs[stack.name] = env
    return _envs[stack.name]


def _context(spec: ProjectSpec, stack: Stack, **extra: Any) -> dict[str, Any]:
    return {
        "spec": spec,
        "stack": stack,
        "access_text": lambda e: access_text(e, spec.auth),
        "entities_json": pprint.pformat(smoke_cases(spec), width=96),
        "has_rule_tests": False,
        "has_edge_tests": False,
        **extra,
    }


def plan_files(spec: ProjectSpec) -> list[dict[str, str]]:
    """Ordered file plan: (path, owner agent, purpose). Order = dependency order."""
    stack = get_stack(spec.stack)
    entity_paths = {p for e in spec.entities for p in _entity_paths(e)}
    return [
        {"path": path, "owner": "entity" if path in entity_paths else "template", "purpose": purpose}
        for path, _, purpose in stack.files(spec)
    ]


def _entity_paths(e: EntitySpec) -> list[str]:
    return [f"app/{kind}/{e.module}.py" for kind in ("models", "schemas", "services", "routers")]


def render_entity(spec: ProjectSpec, e: EntitySpec) -> dict[str, str]:
    """The four reference files of one entity."""
    stack = get_stack(spec.stack)
    env = _env(stack)
    ctx = _context(spec, stack, e=e, actions=spec.actions_for(e), search_field=search_field(e))
    names = {"models": "model.py.j2", "schemas": "schema.py.j2", "services": "service.py.j2", "routers": "router.py.j2"}
    return {f"app/{kind}/{e.module}.py": env.get_template(t).render(**ctx) for kind, t in names.items()}


def render_project(spec: ProjectSpec, **extra: Any) -> dict[str, str]:
    """Render every planned file from templates (the deterministic baseline)."""
    stack = get_stack(spec.stack)
    env = _env(stack)
    ctx = _context(spec, stack, **extra)
    files: dict[str, str] = {}
    for e in spec.entities:
        files.update(render_entity(spec, e))
    for path, template, _ in stack.files(spec):
        if path in files:
            continue
        if template is None:
            files[path] = ""
        elif template == "@openapi":
            files[path] = json.dumps(openapi_document(spec), indent=1) + "\n"
        else:
            files[path] = env.get_template(template).render(**ctx)
    return files


# --------------------------------------------------------------------------- contract test data


def _sample_value(e: EntitySpec, f: FieldSpec) -> Any:
    if f.references:
        return f"@ref:{f.references}"
    if f.choices:
        return f.choices[0]
    if f.type in ("str", "text"):
        return f"sample {e.module} {f.name}"
    value = _SAMPLES[f.type]
    if f.is_number:
        if f.min is not None and value < f.min:
            value = f.min if f.type != "int" else int(f.min)
        if f.max is not None and value > f.max:
            value = f.max if f.type != "int" else int(f.max)
    return value


def _invalid_payload(e: EntitySpec) -> dict[str, Any] | None:
    """A body that must be rejected with 422: the first field with a value of the wrong type."""
    for f in e.fields:
        if f.choices:
            return {f.name: "not-one-of-the-choices"}
        if f.is_number and f.min is not None:
            return {f.name: f.min - 1}
        return {f.name: _WRONG.get(f.type, "not-valid")}
    return None


def smoke_cases(spec: ProjectSpec) -> list[dict[str, Any]]:
    cases = []
    for e in spec.entities:
        payload = {f.name: _sample_value(e, f) for f in e.fields}
        update: dict[str, Any] = {}
        sf = search_field(e)
        if sf:
            update[sf] = f"updated {e.module} {sf}"
        cases.append({"name": e.name, "table": e.table, "access": e.write_access, "payload": payload,
                      "update": update, "invalid": _invalid_payload(e)})
    return cases


# --------------------------------------------------------------------------- OpenAPI for Flask / Django


def _schema(e: EntitySpec, read: bool) -> dict[str, Any]:
    kinds = {"str": "string", "text": "string", "int": "integer", "float": "number", "decimal": "number",
             "bool": "boolean", "date": "string", "datetime": "string"}
    props: dict[str, Any] = {f.name: {"type": kinds[f.type], **({"enum": f.choices} if f.choices else {})}
                             for f in e.fields}
    if read:
        props = {"id": {"type": "integer"}, **props, "created_at": {"type": "string"}}
    required = [f.name for f in e.fields if f.required and not read]
    return {"type": "object", "properties": props, **({"required": required} if required else {})}


def openapi_document(spec: ProjectSpec) -> dict[str, Any]:
    """A small OpenAPI 3 description, so Flask and Django projects get /docs like FastAPI does."""
    def body(ref: str) -> dict[str, Any]:
        return {"required": True, "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{ref}"}}}}

    def ok(code: str, desc: str) -> dict[str, Any]:
        return {code: {"description": desc}}

    item = [{"name": "item_id", "in": "path", "required": True, "schema": {"type": "integer"}}]
    paths: dict[str, Any] = {"/health": {"get": {"tags": ["meta"], "responses": ok("200", "ok")}}}
    schemas: dict[str, Any] = {}
    security = [{"bearer": []}] if spec.auth else []
    if spec.auth:
        schemas["Credentials"] = {"type": "object", "required": ["email", "password"],
                                  "properties": {"email": {"type": "string"}, "password": {"type": "string"}}}
        paths["/auth/register"] = {"post": {"tags": ["auth"], "requestBody": body("Credentials"), "responses": ok("201", "created")}}
        paths["/auth/login"] = {"post": {"tags": ["auth"], "requestBody": body("Credentials"), "responses": ok("200", "token")}}
        paths["/auth/me"] = {"get": {"tags": ["auth"], "security": security, "responses": ok("200", "current user")}}
    for e in spec.entities:
        schemas[f"{e.name}Create"] = _schema(e, read=False)
        tag = [e.name]
        paths[f"/{e.table}/"] = {
            "get": {"tags": tag, "responses": ok("200", f"list of {e.name}")},
            "post": {"tags": tag, "security": security, "requestBody": body(f"{e.name}Create"), "responses": ok("201", "created")},
        }
        paths[f"/{e.table}/{{item_id}}"] = {
            "get": {"tags": tag, "parameters": item, "responses": ok("200", e.name)},
            "patch": {"tags": tag, "parameters": item, "security": security, "requestBody": body(f"{e.name}Create"), "responses": ok("200", "updated")},
            "delete": {"tags": tag, "parameters": item, "security": security, "responses": ok("204", "deleted")},
        }
        for a in spec.actions_for(e):
            op = {"tags": tag, "summary": a.description, "security": security, "responses": ok("200", "; ".join(a.rules) or "done")}
            if a.on_item:
                op["parameters"] = item
            paths.setdefault(f"/{e.table}{a.path}", {})[a.method.lower()] = op
    doc: dict[str, Any] = {
        "openapi": "3.0.3",
        "info": {"title": spec.project_name, "description": spec.description, "version": "0.1.0"},
        "servers": [{"url": "."}],
        "paths": paths,
        "components": {"schemas": schemas},
    }
    if spec.auth:
        doc["components"]["securitySchemes"] = {"bearer": {"type": "http", "scheme": "bearer"}}
    return doc
