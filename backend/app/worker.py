"""A build worker: python -m app.worker

Takes queued runs from Redis (RUN_QUEUE=redis, REDIS_URL) and builds WORKER_CONCURRENCY of them at a
time. Start as many workers as you like, on any machine that reaches the same Redis and Postgres.
A worker that dies (crash, redeploy, scale-down) stops refreshing its claims; another worker then
continues those runs from their last checkpoint. On SIGTERM it takes no new runs and waits up to
WORKER_DRAIN_SECONDS for the current ones.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import sys
import threading
import time

from agent.config import settings

from .ops import setup_logging
from .queue import CLAIM_SECONDS, RunQueue
from .runs import RunManager
from .store import Store

log = logging.getLogger("vibestack.worker")
SWEEP_SECONDS = CLAIM_SECONDS  # how often to look for runs whose worker died


class Worker:
    def __init__(self, manager: RunManager, queue: RunQueue, concurrency: int, name: str = "") -> None:
        self.manager, self.queue = manager, queue
        self.name = name or f"{socket.gethostname()}:{os.getpid()}"
        self.concurrency = concurrency
        self.slots = threading.BoundedSemaphore(concurrency)
        self.building: dict[str, threading.Thread] = {}
        self.stopping = threading.Event()
        self._last_sweep = 0.0

    def tick(self, pop_timeout: int = 5) -> str | None:
        """One loop step: keep claims alive, re-queue orphans now and then, start one job if a slot is free."""
        for run_id in list(self.building):
            if self.building[run_id].is_alive():
                self.queue.heartbeat(run_id, self.name)
            else:
                self.building.pop(run_id)
        if time.monotonic() - self._last_sweep > SWEEP_SECONDS:
            self._last_sweep = time.monotonic()
            self.manager.requeue_orphans()
        if not self.slots.acquire(timeout=1):
            return None
        job = self.queue.pop(timeout=pop_timeout)
        if job is None:
            self.slots.release()
            return None
        thread = threading.Thread(target=self._build, args=(job,), daemon=True, name=f"run-{job['run_id']}")
        self.building[job["run_id"]] = thread
        thread.start()
        return job["run_id"]

    def _build(self, job: dict) -> None:
        try:
            if self.manager.execute_job(job, self.name):
                log.info("run %s finished", job["run_id"])
        except Exception:
            log.exception("run %s crashed the worker thread", job["run_id"])
        finally:
            self.slots.release()

    def serve(self, drain_seconds: float) -> None:
        log.info("worker %s ready: %d builds at a time", self.name, self.concurrency)
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 - Redis restarting or a network blip: wait and go on
                log.warning("queue unavailable (%s); retrying", exc)
                self.stopping.wait(2)
        deadline = time.monotonic() + drain_seconds
        while any(t.is_alive() for t in self.building.values()) and time.monotonic() < deadline:
            for run_id in list(self.building):
                self.queue.heartbeat(run_id, self.name)
            time.sleep(1)
        log.info("worker %s stopped", self.name)


def main() -> None:
    setup_logging()
    if settings.run_queue != "redis" or not settings.redis_url:
        raise SystemExit("set RUN_QUEUE=redis and REDIS_URL to run a worker")
    queue = RunQueue(settings.redis_url)
    if "--check" in sys.argv:  # liveness probe: can this worker reach Redis and the database?
        queue.r.ping()
        Store(settings.platform_database_url).ping()
        raise SystemExit(0)
    worker = Worker(RunManager(Store(settings.platform_database_url), queue, executes=True), queue, settings.worker_concurrency)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: worker.stopping.set())
    worker.serve(float(os.environ.get("WORKER_DRAIN_SECONDS", "25")))


if __name__ == "__main__":
    main()
