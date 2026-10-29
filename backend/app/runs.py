"""Run manager: executes graph runs on worker threads and buffers their events.

Runs are decoupled from HTTP connections: a client can disconnect and
reconnect to the event stream (Last-Event-ID) without affecting the run.
Graph state is persisted by the checkpointer (SQLite or Postgres), so finished runs can still
be inspected after a server restart, and interrupted runs continue where they stopped (recover).

With a queue (RUN_QUEUE=redis, see app/queue.py) the web server only queues runs, and worker
processes (app/worker.py) build them; a run's events and status then live in Redis, so any web
server can stream them.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from langgraph.types import Command

from agent.config import settings
from agent.graph import build_graph, checkpointer, initial_state
from agent.llm import RunStopped, stop_check

from .queue import RunQueue
from .store import Store

STOP_GRACE_S = 20  # after Stop, a run ends for the user within this many seconds whatever the worker does
log = logging.getLogger(__name__)

TERMINAL = {"succeeded", "failed", "error"}
WAITING = "awaiting_approval"  # paused before any code is written, until the user approves the design


@dataclass
class Run:
    id: str
    prompt: str
    created_at: float = field(default_factory=time.time)
    local_status: str = "started"
    events: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    worker: threading.Thread | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    finished_by_watchdog: bool = False
    queue: RunQueue | None = None  # set: events and status live in Redis, shared by every process

    @property
    def status(self) -> str:
        if self.queue is not None:
            return self.queue.status(self.id) or self.local_status
        return self.local_status

    @status.setter
    def status(self, value: str) -> None:
        self.local_status = value
        if self.queue is not None:
            self.queue.set_status(self.id, value)

    def emit(self, event: dict[str, Any]) -> None:
        event["ts"] = time.time()
        if self.queue is not None:
            self.queue.add_event(self.id, event)
            return
        with self.lock:
            event["seq"] = len(self.events)
            self.events.append(event)

    def events_since(self, seq: int) -> list[dict[str, Any]]:
        if self.queue is not None:
            return self.queue.events(self.id, seq)
        with self.lock:
            return self.events[seq:]

    @property
    def active(self) -> bool:
        if self.queue is not None and self.worker is None:  # built by a worker process
            return self.status in ("queued", "running")
        return self.worker is not None and self.worker.is_alive()


class RunManager:
    """executes=True: this process builds runs (a worker, or a web server without a queue)."""

    def __init__(self, store: Store, queue: RunQueue | None = None, executes: bool | None = None) -> None:
        self.graph = build_graph(checkpointer())
        self.store = store
        self.queue = queue
        self.executes = queue is None if executes is None else executes
        self.runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        if queue is not None and not settings.uses_postgres:
            log.warning("RUN_QUEUE=redis without Postgres: web and workers only share runs on one machine")

    def _config(self, run_id: str) -> dict[str, Any]:
        # 3 graph steps per repair; set explicitly so a long healing run never hits LangGraph's step limit.
        return {"configurable": {"thread_id": run_id}, "recursion_limit": 40 + 3 * 30}

    def cancel(self, run_id: str) -> None:
        """Stop a run: it ends after the agent working now (a run waiting for approval ends at once)."""
        record = self.store.run(run_id)
        if record is None or record["status"] != "running":
            raise ValueError("this run is not running")
        run = self.get_run(run_id)
        if self.waiting_for_approval(run_id) and not (run and run.worker and run.worker.is_alive()):
            values = self.graph.get_state(self._config(run_id)).values
            self.store.finish_run(run_id, "failed", values or {})
            if run is not None:
                run.status = "failed"
                run.emit({"type": "status", "status": "failed"})
            return
        if run is not None:
            run.cancel.set()
            run.emit({"type": "status", "status": "stopping"})
            threading.Thread(target=self._stop_watchdog, args=(run,), daemon=True).start()
        if self.queue is not None:
            self.queue.request_cancel(run_id)

    def _stop_watchdog(self, run: Run) -> None:
        """Agents notice Stop within a second or two. If the worker is stuck in something that cannot be
        interrupted (a long test run), the run still ends for the user after STOP_GRACE_S."""
        worker = run.worker
        if worker is None:
            return
        worker.join(STOP_GRACE_S)
        if worker.is_alive() and run.status == "running":
            log.warning("run %s did not stop within %ss; marking it stopped", run.id, STOP_GRACE_S)
            run.finished_by_watchdog = True
            run.status = "failed"
            try:
                values = self.graph.get_state(self._config(run.id)).values
            except Exception:  # noqa: BLE001 - the run is still recorded as stopped
                values = {}
            self.store.finish_run(run.id, "failed", values or {})
            run.emit({"type": "error", "message": "Stopped: stopped by you. The last finished step is kept."})
            run.emit({"type": "status", "status": "failed"})

    def _stop_reason(self, run: Run, started: float) -> str | None:
        if run.cancel.is_set() or (self.queue is not None and self.queue.cancel_requested(run.id)):
            return "stopped by you"
        if time.monotonic() - started > settings.run_deadline_s:
            return f"the build took longer than {settings.run_deadline_s // 60} minutes"
        return None

    def active_count(self) -> int:
        if self.queue is not None and not self.executes:
            return len(self.store.unfinished_runs())
        return sum(1 for r in self.runs.values() if r.active)

    def get_run(self, run_id: str) -> Run | None:
        """The run, also when another process (a worker) builds it."""
        run = self.runs.get(run_id)
        if run is None and self.queue is not None and self.queue.known(run_id):
            record = self.store.run(run_id) or {}
            run = self.runs.setdefault(run_id, Run(id=run_id, prompt=record.get("prompt", ""), queue=self.queue))
        return run

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
            if self.executes and self.active_count() >= settings.max_concurrent_runs:
                raise RuntimeError("too many runs in progress, try again shortly")
            base = None
            if base_run_id:
                values = self.graph.get_state(self._config(base_run_id)).values
                if values.get("spec"):
                    base = {"run_id": base_run_id, "spec": values["spec"], "files": dict(values.get("files") or {}),
                            "template_files": dict(values.get("template_files") or {}),
                            "plugin_choices": list(values.get("plugin_choices") or []),
                            "failed": values.get("status") != "succeeded"}
                elif values.get("prompt"):  # it stopped before a design existed: start over with both requests
                    prompt = f"{values['prompt']}\n\nAlso: {prompt}"
                    options = {**options, "resume": False}
            state = initial_state(prompt, options, spec, base)
            run = Run(id=state["run_id"], prompt=prompt, queue=self.queue)
            self.runs[run.id] = run
            self.store.add_run(run.id, owner_id, prompt, options.get("stack", "fastapi"), project_id, base_run_id)
            if self.executes:
                self._launch(run, state)
            else:
                self.queue.push(run.id, state)
                run.emit({"type": "progress", "node": "parse_spec", "text": "Waiting for a free builder"})
        return run

    def recover(self) -> list[str]:
        """After a restart or redeploy: continue every interrupted run from its last checkpoint.

        LangGraph saved the state after each agent, so a run picks up at the next agent; a run that
        never reached its first checkpoint is marked as an error instead.
        """
        if not self.executes:
            return []  # the workers continue interrupted runs (requeue_orphans)
        resumed = []
        for row in self.store.unfinished_runs():
            if row["id"] in self.runs:
                continue
            snapshot = self.graph.get_state(self._config(row["id"]))
            run = Run(id=row["id"], prompt=row["prompt"], created_at=row["created_at"])
            self.runs[run.id] = run
            if not snapshot.values or not snapshot.next:
                run.status = "error"
                self.store.finish_run(run.id, "error", snapshot.values or {})
                continue
            run.emit({"type": "progress", "node": snapshot.next[0], "text": "Resumed after a server restart"})
            self._launch(run, None)  # None = continue from the checkpoint
            resumed.append(run.id)
        if resumed:
            log.info("resumed %d interrupted run(s): %s", len(resumed), ", ".join(resumed))
        return resumed

    def project_busy(self, project_id: str) -> bool:
        if self.queue is not None:
            return self.store.project_running(project_id)
        return any(r.active and self.store.run(r.id)["project_id"] == project_id for r in self.runs.values())

    # ------------------------------------------------------------------ worker side (app/worker.py)

    def execute_job(self, job: dict[str, Any], worker: str) -> bool:
        """Build one queued run in this thread. False when another worker has it or it is already done."""
        run_id, graph_input = job["run_id"], job.get("input")
        if not self.queue.claim(run_id, worker):
            return False
        try:
            record = self.store.run(run_id)
            if record is None or record["status"] != "running":
                return False
            run = Run(id=run_id, prompt=record["prompt"], created_at=record["created_at"], queue=self.queue)
            self.runs[run_id] = run
            if graph_input is None:  # continue an interrupted run from its last checkpoint
                snapshot = self.graph.get_state(self._config(run_id))
                if not snapshot.values or not snapshot.next:
                    run.status = "error"
                    self.store.finish_run(run_id, "error", snapshot.values or {})
                    run.emit({"type": "status", "status": "error"})
                    return True
                run.emit({"type": "progress", "node": snapshot.next[0], "text": "Continued by another builder"})
            run.status = "running"
            run.emit({"type": "status", "status": "running"})
            if isinstance(graph_input, dict) and "resume" in graph_input and len(graph_input) == 1:
                graph_input = Command(resume=graph_input["resume"])  # the user approved the design
            self._execute(run, graph_input)
            return True
        finally:
            self.queue.release(run_id)
            self.runs.pop(run_id, None)

    def waiting_for_approval(self, run_id: str) -> bool:
        snapshot = self.graph.get_state(self._config(run_id))
        return bool(snapshot.next) and any(getattr(t, "interrupts", ()) for t in snapshot.tasks)

    def approve(self, run_id: str, decision: dict[str, Any]) -> None:
        """Continue a run paused at the design review: {} approves, {"spec": ...} approves an edited design,
        {"instruction": "..."} sends the design back to the Architect with a change in words."""
        if not self.waiting_for_approval(run_id):
            raise ValueError("this run is not waiting for approval")
        if self.executes:
            record = self.store.run(run_id) or {}
            run = self.runs.get(run_id) or self.runs.setdefault(run_id, Run(id=run_id, prompt=record.get("prompt", "")))
            if run.active:
                raise ValueError("this run is already continuing")
            self._launch(run, Command(resume=decision))
        else:
            run = self.get_run(run_id)
            self.queue.push(run_id, {"resume": decision})
            if run is not None:
                run.emit({"type": "progress", "node": "plan", "text": "Design approved; waiting for a free builder"})

    def prune(self, days: int) -> int:
        """Retention: drop the saved agent state (checkpoints, the biggest data) of finished builds older than
        `days`. A project's current version is always kept; zips, the version list and the numbers stay."""
        pruned = 0
        for run_id in self.store.prunable_runs(time.time() - days * 86400):
            self.graph.checkpointer.delete_thread(run_id)
            self.store.mark_pruned(run_id)
            pruned += 1
        if pruned:
            log.info("retention: removed the saved state of %d old build(s)", pruned)
        return pruned

    def requeue_orphans(self) -> list[str]:
        """Runs still marked running that no worker holds (it crashed or was redeployed): queue them again."""
        orphans = []
        for row in self.store.unfinished_runs():
            if self.queue.claimed(row["id"]) or self.queue.status(row["id"]) in ("queued", WAITING):
                continue
            self.queue.push(row["id"], None)
            orphans.append(row["id"])
        if orphans:
            log.info("queued %d interrupted run(s) again: %s", len(orphans), ", ".join(orphans))
        return orphans

    def _launch(self, run: Run, graph_input: Any) -> None:
        run.status = "running"
        run.emit({"type": "status", "status": "running"})
        run.worker = threading.Thread(target=self._execute, args=(run, graph_input), daemon=True)
        run.worker.start()

    def _execute(self, run: Run, graph_input: Any) -> None:
        config = self._config(run.id)
        started, values, stopped = time.monotonic(), None, None
        # Model calls, tool loops and parallel jobs check this every second (agent.llm.raise_if_stopped), so
        # Stop and the run deadline end a run even while an agent is stuck waiting on a model.
        stop_token = stop_check.set(lambda: self._stop_reason(run, started))
        try:
            try:
                stopped = self._stream(run, graph_input, config, started)
            except RunStopped as exc:
                stopped = str(exc) or "stopped by you"
            values = self.graph.get_state(config).values
            if stopped:
                run.status = "failed"
                run.emit({"type": "error", "message": f"Stopped: {stopped}. The last finished step is kept."})
            elif self.waiting_for_approval(run.id):
                run.status = WAITING
                run.emit({"type": "status", "status": WAITING})
                return  # not finished: the user approves the design, then approve() continues it
            else:
                run.status = values.get("status", "succeeded")
                if run.status not in TERMINAL:
                    run.status = "failed"
        except Exception as exc:  # any crash is reported to the UI instead of killing the worker
            log.exception("run %s crashed", run.id)
            run.status = "error"
            run.emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            try:
                values = self.graph.get_state(config).values
            except Exception:  # noqa: BLE001 - the run is still recorded as finished below
                values = None
        finally:
            stop_check.reset(stop_token)
        if run.finished_by_watchdog:  # Stop already ended it in the UI; just record the final state
            self.store.finish_run(run.id, "failed", values or {})
            return
        self.store.finish_run(run.id, run.status, values or {})
        run.emit({"type": "status", "status": run.status})

    def _stream(self, run: Run, graph_input: Any, config: dict[str, Any], started: float) -> str | None:
        """Run the graph and forward its events to the UI; returns why it stopped early, if it did."""
        for mode, update in self.graph.stream(graph_input, config, stream_mode=["updates", "custom"]):
            stopped = self._stop_reason(run, started)
            if stopped:
                return stopped
            if mode == "custom":
                if update.get("kind") == "stream":  # text a model is writing right now (agent.ledger.LiveText)
                    run.emit({"type": "stream", "agent": update.get("agent"), "label": update.get("label"),
                              "delta": update.get("delta", ""), "done": bool(update.get("done"))})
                else:  # what an agent is doing right now (agent.ledger.progress)
                    run.emit({"type": "progress", "node": update.get("node"), "text": update.get("text")})
                continue
            for node, delta in update.items():
                if node.startswith("__"):  # "__interrupt__": the run paused (see waiting_for_approval)
                    continue
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
        return None

    def snapshot(self, run_id: str) -> dict[str, Any] | None:
        values = self.graph.get_state(self._config(run_id)).values
        run = self.get_run(run_id)
        if not values:
            record = self.store.run(run_id) if run is None else None
            if record and record["status"] == "pruned":  # its details were removed by the retention policy
                return {"run_id": run_id, "project_id": record.get("project_id"), "version": record.get("version"),
                        "prompt": record["prompt"], "status": "pruned", "files": [], "ledger": [], "pruned": True}
            if run is None:
                return None
            # Started, but the first checkpoint has not been written yet.
            values = {"prompt": run.prompt, "status": run.status}
        status = run.status if run else values.get("status")
        if status not in TERMINAL and values.get("status") == "spec_ready" and self.waiting_for_approval(run_id):
            status = WAITING
        queued = status == "queued"
        if queued:  # for the UI a queued run is already running: its first agent just has not started
            status = "running"
        record = self.store.run(run_id) or {}
        return {
            "run_id": run_id,
            "project_id": record.get("project_id"),
            "version": record.get("version"),
            "prompt": values.get("prompt"),
            "status": status,
            "queued": queued,
            "options": values.get("options"),
            "spec": values.get("spec"),
            "file_plan": values.get("file_plan"),
            "plugin_choices": values.get("plugin_choices") or [],
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
            "error_class": values.get("error_class"),
            "budget_usd": _budget(values.get("spec")),
            "report": values.get("report"),
            "has_artifact": bool(values.get("artifact_path")),
            # The verified template build (every screen, actions still stubbed) can be previewed as a draft.
            "has_draft": bool(values.get("template_files")),
        }

    def files(self, run_id: str) -> dict[str, str]:
        return dict(self.graph.get_state(self._config(run_id)).values.get("files") or {})

    def draft_files(self, run_id: str) -> dict[str, str]:
        """The verified template build of a run: works at once, with custom actions not written yet."""
        return dict(self.graph.get_state(self._config(run_id)).values.get("template_files") or {})

    def file(self, run_id: str, path: str) -> str | None:
        values = self.graph.get_state(self._config(run_id)).values
        return (values.get("files") or {}).get(path)

    def artifact(self, run_id: str) -> str | None:
        return self.graph.get_state(self._config(run_id)).values.get("artifact_path")


def _budget(spec: dict | None) -> float:
    """This build's money budget (bigger apps get more, see healing.scaled_limits)."""
    from agent.healing import scaled_limits
    from agent.state import ProjectSpec

    if not spec:
        return settings.max_cost_per_run_usd
    try:
        return round(scaled_limits(ProjectSpec.model_validate(spec), settings.max_heal_safety,
                                   settings.max_cost_per_run_usd)[1], 2)
    except ValueError:
        return settings.max_cost_per_run_usd


def _trim_validation(v: dict[str, Any] | None) -> dict[str, Any] | None:
    if not v:
        return None
    return {**v, "log": (v.get("log") or "")[-4000:]}
