"""The web frameworks VibeStack can generate: FastAPI, Flask and Django.

Every stack uses the same project layout (app/models, app/schemas, app/services,
app/routers, tests/) and the same HTTP contract, so the spec-derived tests, the
error classifier and the self-healing loop work unchanged for all three. A stack
only describes what really differs: its templates, its file list, how to import
and start the app, and which packages it needs.
"""

from __future__ import annotations

from dataclasses import dataclass

from .state import EntitySpec, ProjectSpec

# (path, template name or None for an empty file, purpose)
FileRow = tuple[str, str | None, str]


@dataclass
class Stack:
    name: str
    label: str
    template_dirs: list[str]  # searched in order, under agent/templates/
    cmd: dict[str, str]  # shell commands shown in the README / Makefile / Dockerfile
    import_check: str  # Python code that imports the whole app (validation gate 1)
    prepare: list[list[str]]  # python commands to run before booting (e.g. Django migrate)
    uses_sqlalchemy: bool
    migration_steps: list[list[str]]  # python commands that create + apply the first migration
    migration_dir: str  # where those migration files appear

    def boot_args(self, port: int, root_path: str = "", threaded: bool = True) -> list[str]:
        """Arguments after `python` that start a development server on 127.0.0.1:<port>.

        The boot gate only asks for /health, so it runs Flask and Django single-threaded
        (fewer threads in the sandbox); the live preview keeps threads for live updates.
        """
        if self.name == "fastapi":
            args = ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)]
            return args + (["--root-path", root_path] if root_path else [])
        if self.name == "flask":
            args = ["-m", "flask", "--app", "app.main:app", "run", "--host", "127.0.0.1", "--port", str(port)]
            return args + ([] if threaded else ["--without-threads"])
        return ["manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"] + ([] if threaded else ["--nothreading"])

    def dependencies(self, spec: ProjectSpec) -> list[str]:
        """Runtime packages of the generated project."""
        deps = {
            "fastapi": ["fastapi>=0.110", "uvicorn[standard]>=0.28", "sqlalchemy>=2.0", "alembic>=1.13"],
            "flask": ["flask>=3.0", "gunicorn>=22.0", "sqlalchemy>=2.0", "alembic>=1.13"],
            "django": ["django>=5.1", "gunicorn>=22.0"],
        }[self.name]
        deps = deps + ["pydantic>=2.6", "psycopg[binary]>=3.1"]
        return deps + (["pyjwt>=2.8"] if spec.auth else []) + (["redis>=5.0"] if spec.has("rate_limit") else [])

    def files(self, spec: ProjectSpec) -> list[FileRow]:
        """Every file of the generated project, in dependency order."""
        rows: list[FileRow] = [
            ("app/__init__.py", None, "package marker"),
            ("app/config.py", "config.py.j2", "settings from environment variables"),
            ("app/logs.py", "logs.py.j2", "JSON log lines with request ids"),
            ("app/errors.py", "errors.py.j2", "ApiError -> RFC 7807 problem responses, state-machine check"),
            ("app/events.py", "events.py.j2", "live updates (Server-Sent Events)"),
        ]
        if spec.has("rate_limit"):
            rows.append(("app/ratelimit.py", "ratelimit.py.j2", "token-bucket rate limits (Redis when REDIS_URL is set)"))
        if self.name == "django":
            rows += [
                ("manage.py", "manage.py.j2", "Django command line"),
                ("app/settings.py", "settings.py.j2", "Django settings"),
                ("app/middleware.py", "middleware.py.j2", "JSON errors, CORS, request log"),
            ]
        else:
            rows.append(("app/database.py", "database.py.j2", "engine, session, Base"))
        if spec.auth:
            rows += [
                ("app/models/user.py", "user_model.py.j2", "User table"),
                ("app/schemas/user.py", "user_schema.py.j2", "auth request/response shapes"),
                ("app/security.py", "security.py.j2", "password hashing (scrypt), JWT, roles"),
            ]
            if spec.has("refresh_tokens"):
                rows += [
                    ("app/models/refresh_token.py", "refresh_token_model.py.j2", "refresh tokens (hashed)"),
                    ("app/tokens.py", "tokens.py.j2", "refresh-token rotation and reuse detection"),
                ]
            if spec.has("audit_log"):
                rows += [
                    ("app/models/audit_log.py", "audit_log_model.py.j2", "immutable audit log table"),
                    ("app/audit.py", "audit.py.j2", "writes audit entries"),
                    ("app/routers/audit.py", "audit_router.py.j2", "read-only audit log for admins"),
                ]
            rows.append(("app/routers/auth.py", "auth_router.py.j2", "register/login/me" + (", refresh/logout" if spec.has("refresh_tokens") else "")))
        for e in spec.entities:
            rows += [(path, template, purpose) for path, template, purpose in entity_rows(e)]
        rows += [
            ("app/models/__init__.py", "models_init.py.j2", "model registry"),
            ("app/schemas/__init__.py", None, "package marker"),
            ("app/services/__init__.py", "services_init.py.j2", "business logic package"),
            ("app/routers/__init__.py", None, "package marker"),
        ]
        if self.name == "django":
            rows += [
                ("app/urls.py", "urls.py.j2", "URL routes"),
                ("app/wsgi.py", "wsgi.py.j2", "production server entry point"),
            ]
        else:
            rows.append(("app/main.py", "main.py.j2", f"{self.label} app"))
        if self.name != "fastapi":
            rows += [
                ("app/openapi.json", "@openapi", "API description (FastAPI builds its own)"),
                ("app/docs.html", "docs.html.j2", "Swagger UI page"),
            ]
        rows += [
            ("app/static/index.html", "@static:index.html", "frontend page"),
            ("app/static/app.js", "@static:app.js", "frontend app (React, no build step)"),
            ("app/static/vendor/app.css", "@static:vendor/app.css", "styles (Tailwind, prebuilt)"),
            ("app/static/vendor/react.production.min.js", "@static:vendor/react.production.min.js", "React"),
            ("app/static/vendor/react-dom.production.min.js", "@static:vendor/react-dom.production.min.js", "ReactDOM"),
            ("app/static/vendor/htm.umd.js", "@static:vendor/htm.umd.js", "JSX-like templates without a build step"),
            ("app/static/config.json", "@frontend:config", "what the frontend knows about the API"),
            ("app/static/ui.json", "@frontend:ui", "the app's look, written by the Code Generator"),
        ]
        if self.uses_sqlalchemy:
            rows += [
                ("alembic.ini", "alembic.ini.j2", "migration settings"),
                ("migrations/env.py", "migrations_env.py.j2", "migration environment"),
                ("migrations/script.py.mako", "script.py.mako.j2", "migration file template"),
            ]
        rows += [
            ("tests/__init__.py", None, "package marker"),
            ("tests/conftest.py", "conftest.py.j2", "test client + login helpers"),
            ("tests/test_smoke.py", "test_smoke.py.j2", "spec-derived contract tests"),
            ("tests/test_edge_cases.py", "test_edge_cases.py.j2", "spec-derived edge-case tests (no AI)"),
            ("pyproject.toml", "pyproject.toml.j2", "dependencies"),
            ("Dockerfile", "Dockerfile.j2", "container image (non-root)"),
            ("docker-compose.yml", "docker-compose.yml.j2", "api + postgres"),
            (".env.example", "env.example.j2", "config template"),
            (".gitignore", "gitignore.j2", "git ignore rules"),
            ("Makefile", "Makefile.j2", "dev/test/migrate shortcuts"),
            (".github/workflows/ci.yml", "ci.yml.j2", "CI: lint + tests"),
            ("README.md", "README.md.j2", "how to run it"),
        ]
        return rows


def entity_rows(e: EntitySpec) -> list[FileRow]:
    """The four files of one entity: the part the API Engineer agent writes."""
    return [
        (f"app/models/{e.module}.py", "model.py.j2", f"{e.name} table"),
        (f"app/schemas/{e.module}.py", "schema.py.j2", f"{e.name} validation"),
        (f"app/services/{e.module}.py", "service.py.j2", f"{e.name} business rules"),
        (f"app/routers/{e.module}.py", "router.py.j2", f"{e.name} endpoints"),
    ]


def entity_files(e: EntitySpec) -> list[str]:
    return [path for path, _, _ in entity_rows(e)]


# Autogenerate the first revision from the models, then apply it to a fresh database.
ALEMBIC_STEPS = [
    ["-m", "alembic", "revision", "--autogenerate", "-m", "initial schema"],
    ["-m", "alembic", "upgrade", "head"],
]

STACKS: dict[str, Stack] = {
    "fastapi": Stack(
        name="fastapi",
        label="FastAPI",
        template_dirs=["fastapi", "sqlalchemy", "common"],
        cmd={
            "dev": "uvicorn app.main:app --reload",
            "serve": "uvicorn app.main:app --host 0.0.0.0 --port 8000",
            "migrate": "alembic upgrade head",
            "new_migration": 'alembic revision --autogenerate -m "describe the change"',
        },
        import_check="import app.main",
        prepare=[],
        uses_sqlalchemy=True,
        migration_steps=ALEMBIC_STEPS,
        migration_dir="migrations/versions",
    ),
    "flask": Stack(
        name="flask",
        label="Flask",
        template_dirs=["flask", "sqlalchemy", "common"],
        cmd={
            "dev": "flask --app app.main:app run --port 8000",
            "serve": "gunicorn --bind 0.0.0.0:8000 app.main:app",
            "migrate": "alembic upgrade head",
            "new_migration": 'alembic revision --autogenerate -m "describe the change"',
        },
        import_check="import app.main",
        prepare=[],
        uses_sqlalchemy=True,
        migration_steps=ALEMBIC_STEPS,
        migration_dir="migrations/versions",
    ),
    "django": Stack(
        name="django",
        label="Django",
        template_dirs=["django", "common"],
        cmd={
            "dev": "python manage.py migrate --run-syncdb && python manage.py runserver 8000",
            "serve": "gunicorn --bind 0.0.0.0:8000 app.wsgi:application",
            "migrate": "python manage.py migrate --run-syncdb",
            "new_migration": "python manage.py makemigrations app",
        },
        import_check=(
            "import os, django; os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'app.settings'); "
            "django.setup(); import app.urls"
        ),
        prepare=[["manage.py", "migrate", "--run-syncdb", "--verbosity", "0"]],
        uses_sqlalchemy=False,
        migration_steps=[["manage.py", "makemigrations", "app"], ["manage.py", "migrate", "--verbosity", "0"]],
        migration_dir="app/migrations",
    ),
}


def get_stack(name: str) -> Stack:
    if name not in STACKS:
        raise ValueError(f"unknown stack {name!r}; choose one of {', '.join(STACKS)}")
    return STACKS[name]
