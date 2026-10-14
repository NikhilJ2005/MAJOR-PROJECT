"""Platform database (standard library sqlite3): user accounts and run records.

Run *state* (spec, files, ledger) lives in the LangGraph checkpointer; this store
only keeps who owns each run and its headline numbers, so listings, quotas and
/api/stats survive a restart.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
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
);
CREATE INDEX IF NOT EXISTS runs_by_owner ON runs(owner_id, created_at);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()  # one connection shared by API threads and run workers
        with self._lock:
            self._db.executescript(SCHEMA)

    def _query(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._db.execute(sql, args).fetchall()]

    def _write(self, sql: str, args: tuple = ()) -> int:
        with self._lock, self._db:
            return self._db.execute(sql, args).lastrowid or 0

    # -- users ---------------------------------------------------------------------------

    def add_user(self, email: str, password_hash: str) -> int:
        """Raises sqlite3.IntegrityError if the email is taken."""
        return self._write("INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                           (email, password_hash, time.time()))

    def user_by_email(self, email: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM users WHERE email = ?", (email,))
        return rows[0] if rows else None

    def user_by_id(self, user_id: int) -> dict[str, Any] | None:
        rows = self._query("SELECT id, email, created_at FROM users WHERE id = ?", (user_id,))
        return rows[0] if rows else None

    # -- runs ----------------------------------------------------------------------------

    def add_run(self, run_id: str, owner_id: int, prompt: str, stack: str) -> None:
        self._write("INSERT INTO runs (id, owner_id, prompt, stack, status, created_at) VALUES (?, ?, ?, ?, 'running', ?)",
                    (run_id, owner_id, prompt, stack, time.time()))

    def finish_run(self, run_id: str, status: str, values: dict[str, Any]) -> None:
        usage = values.get("usage") or {}
        bugs = values.get("bugs") or []
        self._write(
            "UPDATE runs SET status = ?, finished_at = ?, tokens = ?, cost_usd = ?, repairs = ?, bugs_found = ?, bugs_fixed = ? WHERE id = ?",
            (status, time.time(), int(usage.get("total_tokens", 0)), float(usage.get("cost_usd", 0.0)),
             int(values.get("iteration", 0)), len(bugs), sum(1 for b in bugs if b.get("fixed")), run_id),
        )

    def run_owner(self, run_id: str) -> int | None:
        rows = self._query("SELECT owner_id FROM runs WHERE id = ?", (run_id,))
        return rows[0]["owner_id"] if rows else None

    def runs_for(self, owner_id: int, limit: int = 20) -> list[dict[str, Any]]:
        return self._query("SELECT * FROM runs WHERE owner_id = ? ORDER BY created_at DESC LIMIT ?", (owner_id, limit))

    def runs_since(self, owner_id: int, since: float) -> int:
        return self._query("SELECT COUNT(*) AS n FROM runs WHERE owner_id = ? AND created_at >= ?", (owner_id, since))[0]["n"]

    def stats(self, owner_id: int | None = None) -> dict[str, Any]:
        """Headline numbers for one user (or everyone when owner_id is None)."""
        where, args = ("WHERE owner_id = ?", (owner_id,)) if owner_id is not None else ("", ())
        row = self._query(
            f"""SELECT COUNT(*) AS runs,
                       SUM(status = 'succeeded') AS succeeded,
                       AVG(CASE WHEN finished_at IS NOT NULL THEN repairs END) AS avg_repairs,
                       SUM(bugs_found) AS bugs_found, SUM(bugs_fixed) AS bugs_fixed,
                       SUM(cost_usd) AS cost_usd, SUM(tokens) AS tokens
                FROM runs {where}""",
            args,
        )[0]
        runs = row["runs"] or 0
        return {
            "runs": runs,
            "succeeded": row["succeeded"] or 0,
            "success_rate": round((row["succeeded"] or 0) / runs, 3) if runs else 0.0,
            "avg_repairs": round(row["avg_repairs"] or 0.0, 2),
            "bugs_found": row["bugs_found"] or 0,
            "bugs_fixed": row["bugs_fixed"] or 0,
            "cost_usd": round(row["cost_usd"] or 0.0, 4),
            "tokens": row["tokens"] or 0,
        }
