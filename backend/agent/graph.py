"""The VibeStack harness as a LangGraph state machine of 8 agents.

    Architect -> Planner -> Code Generator -> Validator --ok-----------------------> Packager -> END
    ("keep fixing" starts at the Validator with the previous version's code; with review_design the
    Planner first waits for the user to approve the design, and a change in words goes back to the Architect)
                                              Validator --fail, making progress--> Error Classifier -> Reflector -> Validator
                                              Validator --fail, no progress------> Circuit Breaker -> END

Run from the CLI:
    python -m agent.graph "a todo API with users and tasks" [--stack flask] [--template]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from . import nodes
from .config import settings
from .state import AgentState

NODE_ORDER = [
    "parse_spec",
    "plan",
    "generate",
    "validate",
    "classify",
    "reflect",
    "package",
    "failure_report",
]


def build_graph(checkpointer: BaseCheckpointSaver | None = None):
    g = StateGraph(AgentState)
    for name in NODE_ORDER:
        g.add_node(name, getattr(nodes, name))

    # "Keep fixing" (options.resume) continues healing the given code: straight to the Validator.
    g.add_conditional_edges(START, lambda s: "validate" if (s.get("options") or {}).get("resume") else "parse_spec",
                            ["parse_spec", "validate"])
    g.add_edge("parse_spec", "plan")
    g.add_conditional_edges("plan", lambda s: "parse_spec" if s.get("design_feedback") else "generate",
                            ["parse_spec", "generate"])
    g.add_edge("generate", "validate")
    g.add_conditional_edges("validate", nodes.route_after_validate, ["package", "classify", "failure_report"])
    g.add_edge("classify", "reflect")
    g.add_edge("reflect", "validate")
    g.add_edge("package", END)
    g.add_edge("failure_report", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


def checkpointer():
    """Where every step of every run is saved: Postgres when configured, else SQLite in DATA_DIR."""
    if settings.uses_postgres:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        url = settings.platform_database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        # A pool, not one connection: hosted Postgres (Railway) drops idle connections, and a checkpoint
        # write on a dead connection hangs the run. Connections are checked before use, recycled, and kept
        # alive with TCP keepalives; several runs can save their steps at the same time.
        pool = ConnectionPool(
            url, min_size=1, max_size=10, max_idle=300, max_lifetime=1800, timeout=30, open=True,
            check=ConnectionPool.check_connection,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row, "connect_timeout": 10,
                    "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3},
        )
        saver = PostgresSaver(pool)
        saver.setup()  # creates its tables the first time
        return saver
    from langgraph.checkpoint.sqlite import SqliteSaver

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.checkpoint_db, check_same_thread=False)
    return SqliteSaver(conn)


sqlite_checkpointer = checkpointer  # old name


def initial_state(
    prompt: str, options: dict[str, Any] | None = None, spec: dict | None = None, base: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The input of a run. `base` (run_id, spec, files) makes it a follow-up edit of that version."""
    state: dict[str, Any] = {
        "run_id": uuid.uuid4().hex[:12],
        "prompt": prompt,
        "options": options or {},
        "iteration": 0,
        "ledger": [],
        "usage": {},
        "status": "started",
    }
    if spec:
        state["spec"] = spec
    if base:
        state.update(base_run_id=base["run_id"], base_spec=base["spec"], base_files=base["files"],
                     base_failed=bool(base.get("failed")))
        if state["options"].get("resume"):  # keep fixing: the same design and code, a fresh healing history
            state.update(spec=base["spec"], files=base["files"], template_files=base.get("template_files") or {},
                         plugin_choices=base.get("plugin_choices") or [])
    return state


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate a validated FastAPI backend from a description")
    ap.add_argument("prompt")
    ap.add_argument("--stack", choices=["fastapi", "flask", "django"], default="fastapi")
    ap.add_argument("--template", action="store_true", help="deterministic template codegen (no LLM for code)")
    ap.add_argument("--spec", help="path to a ProjectSpec JSON (skips the LLM spec step)")
    args = ap.parse_args(argv)

    spec = json.loads(Path(args.spec).read_text()) if args.spec else None
    options = {
        "stack": args.stack,
        "codegen_mode": "template" if args.template else settings.codegen_mode,
    }
    state = initial_state(args.prompt, options, spec)
    graph = build_graph()
    config = {"configurable": {"thread_id": state["run_id"]}, "recursion_limit": 130}
    final: dict[str, Any] = {}
    for update in graph.stream(state, config, stream_mode="updates"):
        for node, delta in update.items():
            for e in (delta or {}).get("ledger", []) or []:
                print(f"[{node:>15}] {e['agent']}: {e['action']}" + (f"  ({e['rationale']})" if e.get("rationale") else ""))
    final = graph.get_state(config).values
    print(f"\nstatus: {final.get('status')}  repair attempts: {final.get('iteration', 0)}")
    if final.get("stop_reason"):
        print(f"healing stopped: {final['stop_reason']}")
    print(f"usage: {final.get('usage') or 'no LLM calls'}")
    if final.get("artifact_path"):
        print(f"artifact: {final['artifact_path']}")
    return 0 if final.get("status") == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
