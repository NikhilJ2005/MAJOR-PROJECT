"""Run manager: executes graph runs on worker threads and buffers their events.

Runs are decoupled from HTTP connections: a client can disconnect and
reconnect to the event stream (Last-Event-ID) without affecting the run.
Graph state is persisted by the SQLite checkpointer, so finished runs can still
be inspected and downloaded after a server restart.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from agent.config import settings
from agent.graph import build_graph, checkpointer, initial_state

from .store import Store

log = logging.getLogger(__name__)

TERMINAL = {"succeeded", "failed", "error"}


@dataclass
class Run:
    id: str
    prompt: str
    created_at: float = field(default_factory=time.time)
    status: str = "started"
    events: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    worker: threading.Thread | None = None

    def emit(self, event: dict[str, Any]) -> None:
        with self.lock:
            event["seq"] = len(self.events)
            event["ts"] = time.time()
            self.events.append(event)

    def events_since(self, seq: int) -> list[dict[str, Any]]:
        with self.lock:
            return self.events[seq:]

    @property
    def active(self) -> bool:
        return self.worker is not None and self.worker.is_alive()


class RunManager:
    def __init__(self, store: Store) -> None:
        self.graph = build_graph(checkpointer())
        self.store = store
        self.runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def _config(self, run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}}

    def active_count(self) -> int:
        return sum(1 for r in self.runs.values() if r.active)

    def start(
        self,
        prompt: str,
        options: dict[str, Any],
        owner_id: int,
        spec: dict | None = None,
        project_id: str | None = None,
        base_run_id: str | None = None,
    ) -> Run:
        """Start a run; with base_run_id it is a follow-up edit of that version of the project."""
        with self._lock:
            if self.active_count() >= settings.max_concurrent_runs:
                raise RuntimeError("too many runs in progress, try again shortly")
            base = None
            if base_run_id:
                values = self.graph.get_state(self._config(base_run_id)).values
                if values.get("spec"):
                    base = {"run_id": base_run_id, "spec": values["spec"], "files": dict(values.get("files") or {}),
                            "failed": values.get("status") != "succeeded"}
                elif values.get("prompt"):  # it stopped before a design existed: start over with both requests
                    prompt = f"{values['prompt']}\n\nAlso: {prompt}"
            state = initial_state(prompt, options, spec, base)
            run = Run(id=state["run_id"], prompt=prompt)
            self.runs[run.id] = run
            self.store.add_run(run.id, owner_id, prompt, options.get("stack", "fastapi"), project_id, base_run_id)
            self._launch(run, state)
        return run

    def project_busy(self, project_id: str) -> bool:
        return any(r.active and self.store.run(r.id)["project_id"] == project_id for r in self.runs.values())

    def _launch(self, run: Run, graph_input: Any) -> None:
        run.status = "running"
        run.emit({"type": "status", "status": "running"})
        run.worker = threading.Thread(target=self._execute, args=(run, graph_input), daemon=True)
        run.worker.start()

    def _execute(self, run: Run, graph_input: Any) -> None:
        config = self._config(run.id)
        try:
            for update in self.graph.stream(graph_input, config, stream_mode="updates"):
                for node, delta in update.items():
                    delta = delta or {}
                    run.emit(
                        {
                            "type": "node",
                            "node": node,
                            "status": delta.get("status"),
                            "ledger": delta.get("ledger", []),
                            "files": sorted(delta.get("files", {}) or {}),
                            "validation": _trim_validation(delta.get("validation")),
                            "error_class": delta.get("error_class"),
                            "iteration": delta.get("iteration"),
                            "bugs": delta.get("bugs"),
                            "stop_reason": delta.get("stop_reason"),
                        }
                    )
            values = self.graph.get_state(config).values
            run.status = values.get("status", "succeeded")
            if run.status not in TERMINAL:
                run.status = "failed"
        except Exception as exc:  # any crash is reported to the UI instead of killing the worker
            log.exception("run %s crashed", run.id)
            run.status = "error"
            run.emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            values = self.graph.get_state(config).values
        self.store.finish_run(run.id, run.status, values or {})
        run.emit({"type": "status", "status": run.status})

    def snapshot(self, run_id: str) -> dict[str, Any] | None:
        values = self.graph.get_state(self._config(run_id)).values
        run = self.runs.get(run_id)
        if not values:
            if run is None:
                return None
            # Started, but the first checkpoint has not been written yet.
            values = {"prompt": run.prompt, "status": run.status}
        status = run.status if run else values.get("status")
        record = self.store.run(run_id) or {}
        return {
            "run_id": run_id,
            "project_id": record.get("project_id"),
            "version": record.get("version"),
            "prompt": values.get("prompt"),
            "status": status,
            "options": values.get("options"),
            "spec": values.get("spec"),
            "file_plan": values.get("file_plan"),
            "files": sorted((values.get("files") or {}).keys()),
            "ledger": values.get("ledger", []),
            "validation": _trim_validation(values.get("validation")),
            "iteration": values.get("iteration", 0),
            "usage": values.get("usage", {}),
            "requirements": (values.get("spec") or {}).get("requirements", []),
            "changes": values.get("changes"),
            "base_run_id": values.get("base_run_id"),
            "bugs": values.get("bugs", []),
            "review": values.get("review", []),
            "heal_history": values.get("heal_history", []),
            "stop_reason": values.get("stop_reason"),
            "report": values.get("report"),
            "has_artifact": bool(values.get("artifact_path")),
        }

    def files(self, run_id: str) -> dict[str, str]:
        return dict(self.graph.get_state(self._config(run_id)).values.get("files") or {})

    def file(self, run_id: str, path: str) -> str | None:
        values = self.graph.get_state(self._config(run_id)).values
        return (values.get("files") or {}).get(path)

    def artifact(self, run_id: str) -> str | None:
        return self.graph.get_state(self._config(run_id)).values.get("artifact_path")


def _trim_validation(v: dict[str, Any] | None) -> dict[str, Any] | None:
    if not v:
        return None
    return {**v, "log": (v.get("log") or "")[-4000:]}
