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
from agent.graph import build_graph, initial_state, sqlite_checkpointer

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
    def __init__(self) -> None:
        self.graph = build_graph(sqlite_checkpointer())
        self.runs: dict[str, Run] = {}
        self._lock = threading.Lock()

    def _config(self, run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}}

    def active_count(self) -> int:
        return sum(1 for r in self.runs.values() if r.active)

    def start(self, prompt: str, options: dict[str, Any], spec: dict | None = None) -> Run:
        with self._lock:
            if self.active_count() >= settings.max_concurrent_runs:
                raise RuntimeError("too many runs in progress, try again shortly")
            state = initial_state(prompt, options, spec)
            run = Run(id=state["run_id"], prompt=prompt)
            self.runs[run.id] = run
            self._launch(run, state)
        return run

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
                        }
                    )
            values = self.graph.get_state(config).values
            run.status = values.get("status", "succeeded")
            if run.status not in TERMINAL:
                run.status = "failed"
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            log.exception("run %s crashed", run.id)
            run.status = "error"
            run.emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
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
        return {
            "run_id": run_id,
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
