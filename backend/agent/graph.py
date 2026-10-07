"""The VibeStack harness as a LangGraph state machine.

    parse_spec (architect) -> plan -> generate -> validate
    validate --ok--------------------------------> package -> END
    validate --fail, attempts left--> classify -> reflect -> validate
    validate --fail, budget spent---> failure_report -> END

Run from the CLI:
    python -m agent.graph "a todo API with users and tasks" [--fault] [--template]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
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

    g.add_edge(START, "parse_spec")
    g.add_edge("parse_spec", "plan")
    g.add_edge("plan", "generate")
    g.add_edge("generate", "validate")
    g.add_conditional_edges(
        "validate",
        nodes.route_after_validate,
        {"package": "package", "classify": "classify", "failure_report": "failure_report"},
    )
    g.add_edge("classify", "reflect")
    g.add_edge("reflect", "validate")
    g.add_edge("package", END)
    g.add_edge("failure_report", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())


def sqlite_checkpointer():
    from langgraph.checkpoint.sqlite import SqliteSaver

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.checkpoint_db, check_same_thread=False)
    return SqliteSaver(conn)


def initial_state(prompt: str, options: dict[str, Any] | None = None, spec: dict | None = None) -> dict[str, Any]:
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
    return state


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate a validated FastAPI backend from a description")
    ap.add_argument("prompt")
    ap.add_argument(
        "--fault", nargs="?", const="import", default="none", choices=["none", "import", "status"],
        help="plant a bug to demo self-healing (default kind: import)",
    )
    ap.add_argument("--template", action="store_true", help="deterministic template codegen (no LLM for code)")
    ap.add_argument("--spec", help="path to a ProjectSpec JSON (skips the LLM spec step)")
    args = ap.parse_args(argv)

    spec = json.loads(open(args.spec).read()) if args.spec else None
    options = {
        "fault": args.fault,
        "codegen_mode": "template" if args.template else settings.codegen_mode,
    }
    state = initial_state(args.prompt, options, spec)
    graph = build_graph()
    config = {"configurable": {"thread_id": state["run_id"]}}
    final: dict[str, Any] = {}
    for update in graph.stream(state, config, stream_mode="updates"):
        for node, delta in update.items():
            for e in (delta or {}).get("ledger", []) or []:
                print(f"[{node:>15}] {e['agent']}: {e['action']}" + (f"  ({e['rationale']})" if e.get("rationale") else ""))
    final = graph.get_state(config).values
    print(f"\nstatus: {final.get('status')}  heal iterations: {final.get('iteration', 0)}")
    print(f"usage: {final.get('usage') or 'no LLM calls'}")
    if final.get("artifact_path"):
        print(f"artifact: {final['artifact_path']}")
    return 0 if final.get("status") == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
