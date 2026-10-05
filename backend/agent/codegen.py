"""Deterministic half of code generation: file planning and template rendering.

Templates give every project a known-good skeleton. In LLM mode the model
rewrites the per-entity files, but the rendered templates are kept as the
reference contract and as the last-resort repair source for self-healing.
"""

from __future__ import annotations

import pprint
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from .state import EntitySpec, FieldSpec, ProjectSpec, pluralize, to_snake

TEMPLATE_DIR = Path(__file__).parent / "templates" / "project"

_PY_TYPES = {
    "str": "str",
    "text": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "datetime": "datetime",
}
_SA_TYPES = {
    "str": "String(255)",
    "text": "Text",
    "int": "Integer",
    "float": "Float",
    "bool": "Boolean",
    "datetime": "DateTime",
}
_SAMPLES: dict[str, Any] = {
    "int": 7,
    "float": 1.5,
    "bool": True,
    "datetime": "2026-01-01T00:00:00",
}

_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_env.globals.update(
    py_type=lambda f: _PY_TYPES[f.type],
    sa_type=lambda f: _SA_TYPES[f.type],
    table_of=lambda name: pluralize(to_snake(name)),
)


def search_field(e: EntitySpec) -> str | None:
    for f in e.fields:
        if f.type in ("str", "text") and not f.references:
            return f.name
    return None


def plan_files(spec: ProjectSpec) -> list[dict[str, str]]:
    """Ordered file plan: (path, owner agent, purpose). Order = dependency order."""
    plan: list[dict[str, str]] = [
        {"path": "app/__init__.py", "owner": "template", "purpose": "package marker"},
        {"path": "app/database.py", "owner": "template", "purpose": "engine, session, Base"},
    ]
    if spec.auth:
        plan += [
            {"path": "app/models/user.py", "owner": "template", "purpose": "User table"},
            {"path": "app/schemas/user.py", "owner": "template", "purpose": "auth DTOs"},
            {"path": "app/security.py", "owner": "template", "purpose": "PBKDF2 hashing + JWT"},
            {"path": "app/routers/auth.py", "owner": "template", "purpose": "register/login/me"},
        ]
    for e in spec.entities:
        plan += [
            {"path": f"app/models/{e.module}.py", "owner": "entity", "purpose": f"{e.name} table"},
            {"path": f"app/schemas/{e.module}.py", "owner": "entity", "purpose": f"{e.name} DTOs"},
            {"path": f"app/routers/{e.module}.py", "owner": "entity", "purpose": f"{e.name} CRUD"},
        ]
    plan += [
        {"path": "app/models/__init__.py", "owner": "template", "purpose": "model registry"},
        {"path": "app/schemas/__init__.py", "owner": "template", "purpose": "package marker"},
        {"path": "app/routers/__init__.py", "owner": "template", "purpose": "package marker"},
        {"path": "app/main.py", "owner": "template", "purpose": "FastAPI app + /health"},
        {"path": "tests/__init__.py", "owner": "template", "purpose": "package marker"},
        {"path": "tests/conftest.py", "owner": "template", "purpose": "test client fixture"},
        {"path": "tests/test_smoke.py", "owner": "template", "purpose": "spec-derived contract tests"},
        {"path": "pyproject.toml", "owner": "template", "purpose": "dependencies"},
        {"path": "Dockerfile", "owner": "template", "purpose": "container image"},
        {"path": "docker-compose.yml", "owner": "template", "purpose": "api + postgres"},
        {"path": ".env.example", "owner": "template", "purpose": "config template"},
        {"path": "README.md", "owner": "template", "purpose": "run instructions"},
    ]
    return plan


def _sample_value(e: EntitySpec, f: FieldSpec) -> Any:
    if f.references:
        return f"@ref:{f.references}"
    if f.type in ("str", "text"):
        return f"sample {e.module} {f.name}"
    return _SAMPLES[f.type]


def smoke_cases(spec: ProjectSpec) -> list[dict[str, Any]]:
    cases = []
    for e in spec.entities:
        payload = {f.name: _sample_value(e, f) for f in e.fields}
        update: dict[str, Any] = {}
        sf = search_field(e)
        if sf:
            update[sf] = f"updated {e.module} {sf}"
        cases.append({"name": e.name, "table": e.table, "payload": payload, "update": update})
    return cases


def render_entity(spec: ProjectSpec, e: EntitySpec) -> dict[str, str]:
    ctx = {"spec": spec, "e": e, "search_field": search_field(e)}
    return {
        f"app/models/{e.module}.py": _env.get_template("model.py.j2").render(**ctx),
        f"app/schemas/{e.module}.py": _env.get_template("schema.py.j2").render(**ctx),
        f"app/routers/{e.module}.py": _env.get_template("router.py.j2").render(**ctx),
    }


def render_project(spec: ProjectSpec) -> dict[str, str]:
    """Render every planned file from templates (the deterministic baseline)."""
    r = lambda name, **kw: _env.get_template(name).render(spec=spec, **kw)  # noqa: E731
    files: dict[str, str] = {
        "app/__init__.py": "",
        "app/database.py": r("database.py.j2"),
        "app/models/__init__.py": r("models_init.py.j2"),
        "app/schemas/__init__.py": "",
        "app/routers/__init__.py": "",
        "app/main.py": r("main.py.j2"),
        "tests/__init__.py": "",
        "tests/conftest.py": r("conftest.py.j2"),
        "tests/test_smoke.py": r(
            "test_smoke.py.j2", entities_json=pprint.pformat(smoke_cases(spec), width=96)
        ),
        "pyproject.toml": r("pyproject.toml.j2"),
        "Dockerfile": r("Dockerfile.j2"),
        "docker-compose.yml": r("docker-compose.yml.j2"),
        ".env.example": r("env.example.j2"),
        "README.md": r("README.md.j2"),
    }
    if spec.auth:
        files.update(
            {
                "app/models/user.py": r("user_model.py.j2"),
                "app/schemas/user.py": r("user_schema.py.j2"),
                "app/security.py": r("security.py.j2"),
                "app/routers/auth.py": r("auth_router.py.j2"),
            }
        )
    for e in spec.entities:
        files.update(render_entity(spec, e))
    return files


# A deliberately broken import, used to demo the self-healing loop on demand.
FAULT_FIND = "from sqlalchemy.orm import Session"
FAULT_REPLACE = "from sqlalchemy.orm import Sesion"


def inject_fault(spec: ProjectSpec, files: dict[str, str]) -> tuple[str, str] | None:
    path = f"app/routers/{spec.entities[0].module}.py"
    src = files.get(path, "")
    if FAULT_FIND not in src:
        return None
    return path, src.replace(FAULT_FIND, FAULT_REPLACE, 1)
