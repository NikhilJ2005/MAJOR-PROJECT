"""Platform database: accounts, projects and their versions (runs).

Plain SQL through SQLAlchemy Core, so the same code runs on SQLite (local, default) and on
Postgres (Railway: set PLATFORM_DATABASE_URL). Run *state* (design, files, ledger) lives in
the LangGraph checkpointer; this store keeps who owns what, the version history of each
project and the headline numbers, so listings, quotas and /api/stats survive a restart.

A project is a conversation with the agents: the first prompt builds version 1, every
follow-up prompt ("add reviews") builds the next version from the current one.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError  # noqa: F401  (re-exported: duplicate email)

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY,
        owner_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL,
        stack TEXT NOT NULL,
        current_run_id TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        owner_id INTEGER NOT NULL REFERENCES users(id),
        prompt TEXT NOT NULL,
        stack TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at REAL NOT NULL,
        finished_at REAL,
        tokens INTEGER DEFAULT 0,
        cost_usd REAL DEFAULT 0,
        repairs INTEGER DEFAULT 0,
        bugs_found INTEGER DEFAULT 0,
        bugs_fixed INTEGER DEFAULT 0
    )""",
    # Linked accounts (GitHub). The access token is encrypted (accounts.encrypt).
    """CREATE TABLE IF NOT EXISTS connections (
        user_id INTEGER NOT NULL REFERENCES users(id),
        provider TEXT NOT NULL,
        login TEXT NOT NULL,
        token_enc TEXT NOT NULL,
        created_at REAL NOT NULL,
        PRIMARY KEY (user_id, provider)
    )""",
    "CREATE INDEX IF NOT EXISTS runs_by_owner ON runs(owner_id, created_at)",
    "CREATE INDEX IF NOT EXISTS projects_by_owner ON projects(owner_id, updated_at)",
]
# Columns added after the first release, so older databases are upgraded in place.
RUN_COLUMNS = {"project_id": "TEXT", "parent_run_id": "TEXT", "version": "INTEGER"}
PROJECT_COLUMNS = {"github_repo": "TEXT", "github_pushed_run_id": "TEXT"}


class Store:
    def __init__(self, url: str) -> None:
        sqlite = url.startswith("sqlite")
        self.engine = create_engine(url, connect_args={"check_same_thread": False} if sqlite else {}, pool_pre_ping=True)
        with self.engine.begin() as db:
            for statement in SCHEMA:
                db.execute(text(statement.replace("INTEGER PRIMARY KEY", "SERIAL PRIMARY KEY") if not sqlite else statement))
            for table, columns in (("runs", RUN_COLUMNS), ("projects", PROJECT_COLUMNS)):
                existing = {c["name"] for c in inspect(db).get_columns(table)}
                for name, kind in columns.items():
                    if name not in existing:
                        db.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {kind}"))
        with self.engine.begin() as db:
            db.execute(text("CREATE INDEX IF NOT EXISTS runs_by_project ON runs(project_id, version)"))

    def _rows(self, sql: str, **args: Any) -> list[dict[str, Any]]:
        with self.engine.connect() as db:
            return [dict(r._mapping) for r in db.execute(text(sql), args)]

    def _one(self, sql: str, **args: Any) -> dict[str, Any] | None:
        rows = self._rows(sql, **args)
        return rows[0] if rows else None

    def _write(self, sql: str, **args: Any) -> Any:
        with self.engine.begin() as db:
            result = db.execute(text(sql), args)
            return result.scalar() if result.returns_rows else None

    # -- users ---------------------------------------------------------------------------

    def add_user(self, email: str, password_hash: str) -> int:
        """Raises IntegrityError if the email is taken."""
        return self._write("INSERT INTO users (email, password_hash, created_at) VALUES (:email, :pw, :now) RETURNING id",
                           email=email, pw=password_hash, now=time.time())

    def user_by_email(self, email: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM users WHERE email = :email", email=email)

    def user_by_id(self, user_id: int) -> dict[str, Any] | None:
        return self._one("SELECT id, email, created_at FROM users WHERE id = :id", id=user_id)

    # -- linked accounts -----------------------------------------------------------------

    def save_connection(self, user_id: int, provider: str, login: str, token_enc: str) -> None:
        with self.engine.begin() as db:
            db.execute(text("DELETE FROM connections WHERE user_id = :u AND provider = :p"), {"u": user_id, "p": provider})
            db.execute(text("INSERT INTO connections (user_id, provider, login, token_enc, created_at) "
                            "VALUES (:u, :p, :login, :token, :now)"),
                       {"u": user_id, "p": provider, "login": login, "token": token_enc, "now": time.time()})

    def connection(self, user_id: int, provider: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM connections WHERE user_id = :u AND provider = :p", u=user_id, p=provider)

    # -- projects ------------------------------------------------------------------------

    def add_project(self, owner_id: int, name: str, stack: str) -> str:
        project_id = uuid.uuid4().hex[:12]
        now = time.time()
        self._write("INSERT INTO projects (id, owner_id, name, stack, created_at, updated_at) "
                    "VALUES (:id, :owner, :name, :stack, :now, :now)",
                    id=project_id, owner=owner_id, name=name[:80], stack=stack, now=now)
        return project_id

    def project(self, project_id: str, owner_id: int) -> dict[str, Any] | None:
        return self._one("SELECT * FROM projects WHERE id = :id AND owner_id = :owner", id=project_id, owner=owner_id)

    def projects_for(self, owner_id: int, limit: int = 50) -> list[dict[str, Any]]:
        return self._rows(
            """SELECT p.*, r.status AS current_status, r.version AS current_version,
                      (SELECT COUNT(*) FROM runs v WHERE v.project_id = p.id) AS versions,
                      (SELECT v.status FROM runs v WHERE v.project_id = p.id ORDER BY v.version DESC LIMIT 1) AS latest_status
               FROM projects p LEFT JOIN runs r ON r.id = p.current_run_id
               WHERE p.owner_id = :owner ORDER BY p.updated_at DESC LIMIT :limit""",
            owner=owner_id, limit=limit,
        )

    def versions(self, project_id: str) -> list[dict[str, Any]]:
        return self._rows("SELECT id AS run_id, version, prompt, status, parent_run_id, created_at, finished_at, "
                          "repairs, bugs_found, bugs_fixed, cost_usd FROM runs WHERE project_id = :p ORDER BY version",
                          p=project_id)

    def set_current(self, project_id: str, run_id: str, name: str | None = None) -> None:
        """The version the workspace shows and the next edit starts from."""
        self._write("UPDATE projects SET current_run_id = :run, updated_at = :now, name = COALESCE(:name, name) WHERE id = :id",
                    run=run_id, now=time.time(), name=name, id=project_id)

    def set_github(self, project_id: str, repo: str, run_id: str) -> None:
        """The repository a project is pushed to, and which version is there now."""
        self._write("UPDATE projects SET github_repo = :repo, github_pushed_run_id = :run WHERE id = :id",
                    repo=repo, run=run_id, id=project_id)

    def touch_project(self, project_id: str) -> None:
        self._write("UPDATE projects SET updated_at = :now WHERE id = :id", now=time.time(), id=project_id)

    # -- runs (= versions) ---------------------------------------------------------------

    def add_run(self, run_id: str, owner_id: int, prompt: str, stack: str,
                project_id: str | None = None, parent_run_id: str | None = None) -> int:
        """Record a new run; inside a project it gets the next version number."""
        version = 1
        if project_id:
            last = self._one("SELECT MAX(version) AS v FROM runs WHERE project_id = :p", p=project_id)
            version = (last["v"] or 0) + 1 if last else 1
        self._write("INSERT INTO runs (id, owner_id, prompt, stack, status, created_at, project_id, parent_run_id, version) "
                    "VALUES (:id, :owner, :prompt, :stack, 'running', :now, :project, :parent, :version)",
                    id=run_id, owner=owner_id, prompt=prompt, stack=stack, now=time.time(),
                    project=project_id, parent=parent_run_id, version=version)
        if project_id:
            self.touch_project(project_id)
        return version

    def finish_run(self, run_id: str, status: str, values: dict[str, Any]) -> None:
        usage = values.get("usage") or {}
        bugs = values.get("bugs") or []
        self._write(
            "UPDATE runs SET status = :status, finished_at = :now, tokens = :tokens, cost_usd = :cost, repairs = :repairs, "
            "bugs_found = :found, bugs_fixed = :fixed WHERE id = :id",
            status=status, now=time.time(), tokens=int(usage.get("total_tokens", 0)), cost=float(usage.get("cost_usd", 0.0)),
            repairs=int(values.get("iteration", 0)), found=len(bugs), fixed=sum(1 for b in bugs if b.get("fixed")), id=run_id,
        )
        run = self.run(run_id)
        if run and run["project_id"] and status == "succeeded":
            name = (values.get("spec") or {}).get("project_name", "").replace("_", " ").title() or None
            self.set_current(run["project_id"], run_id, name if run["version"] == 1 else None)

    def run(self, run_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM runs WHERE id = :id", id=run_id)

    def run_metrics(self) -> list[dict[str, Any]]:
        """The numbers /api/metrics adds up, one row per run."""
        return self._rows("SELECT status, created_at, finished_at, repairs, bugs_found, bugs_fixed, tokens, cost_usd FROM runs")

    def unfinished_runs(self) -> list[dict[str, Any]]:
        """Runs a server restart interrupted (still marked running)."""
        return self._rows("SELECT * FROM runs WHERE status = 'running' ORDER BY created_at")

    def run_owner(self, run_id: str) -> int | None:
        row = self.run(run_id)
        return row["owner_id"] if row else None

    def runs_for(self, owner_id: int, limit: int = 20) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM runs WHERE owner_id = :owner ORDER BY created_at DESC LIMIT :limit",
                          owner=owner_id, limit=limit)

    def runs_since(self, owner_id: int, since: float) -> int:
        return self._one("SELECT COUNT(*) AS n FROM runs WHERE owner_id = :owner AND created_at >= :since",
                         owner=owner_id, since=since)["n"]

    def stats(self, owner_id: int | None = None) -> dict[str, Any]:
        """Headline numbers for one user (or everyone when owner_id is None)."""
        where = "WHERE owner_id = :owner" if owner_id is not None else ""
        row = self._one(
            f"""SELECT COUNT(*) AS runs,
                       SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END) AS succeeded,
                       AVG(CASE WHEN finished_at IS NOT NULL THEN repairs END) AS avg_repairs,
                       SUM(bugs_found) AS bugs_found, SUM(bugs_fixed) AS bugs_fixed,
                       SUM(cost_usd) AS cost_usd, SUM(tokens) AS tokens
                FROM runs {where}""",
            owner=owner_id,
        )
        runs = row["runs"] or 0
        return {
            "runs": runs,
            "succeeded": row["succeeded"] or 0,
            "success_rate": round((row["succeeded"] or 0) / runs, 3) if runs else 0.0,
            "avg_repairs": round(float(row["avg_repairs"] or 0.0), 2),
            "bugs_found": row["bugs_found"] or 0,
            "bugs_fixed": row["bugs_fixed"] or 0,
            "cost_usd": round(float(row["cost_usd"] or 0.0), 4),
            "tokens": row["tokens"] or 0,
        }
