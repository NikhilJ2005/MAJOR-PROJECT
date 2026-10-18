"""The build queue: web servers put runs on it, worker processes (python -m app.worker) build them.

Turned on with RUN_QUEUE=redis and REDIS_URL. Everything lives under the "vibestack:" prefix:
- jobs           a list of {run_id, input} waiting for a worker (input None = continue from the checkpoint)
- events:<id>    the run's events in order (the web server streams them to the browser), kept for a day
- status:<id>    queued / running / succeeded / failed / error
- claim:<id>     held by the worker building the run, refreshed while it works (a heartbeat). When it
                 expires the worker died, and another worker continues the run from its last checkpoint.
All processes must share one database (Postgres) so they share the agents' checkpoints.
"""

from __future__ import annotations

import json
from typing import Any

DAY = 86400
CLAIM_SECONDS = 30


class RunQueue:
    def __init__(self, url: str = "", client: Any = None, prefix: str = "vibestack:") -> None:
        if client is None:
            import redis

            # The socket timeout must outlast a blocking pop (redis-py 8 defaults to 5 s).
            client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=30, health_check_interval=30)
        self.r = client
        self.p = prefix

    # ------------------------------------------------------------------ jobs

    def push(self, run_id: str, graph_input: dict[str, Any] | None) -> None:
        self.set_status(run_id, "queued")
        self.r.lpush(f"{self.p}jobs", json.dumps({"run_id": run_id, "input": graph_input}, default=str))

    def pop(self, timeout: int = 5) -> dict[str, Any] | None:
        item = self.r.brpop([f"{self.p}jobs"], timeout=timeout)
        return json.loads(item[1]) if item else None

    def waiting(self) -> int:
        return int(self.r.llen(f"{self.p}jobs"))

    # ------------------------------------------------------------------ claims (one worker per run)

    def claim(self, run_id: str, worker: str) -> bool:
        return bool(self.r.set(f"{self.p}claim:{run_id}", worker, nx=True, ex=CLAIM_SECONDS))

    def heartbeat(self, run_id: str, worker: str) -> None:
        self.r.set(f"{self.p}claim:{run_id}", worker, ex=CLAIM_SECONDS)

    def release(self, run_id: str) -> None:
        self.r.delete(f"{self.p}claim:{run_id}")

    def claimed(self, run_id: str) -> bool:
        return bool(self.r.exists(f"{self.p}claim:{run_id}"))

    # ------------------------------------------------------------------ status and events

    def set_status(self, run_id: str, status: str) -> None:
        self.r.set(f"{self.p}status:{run_id}", status, ex=7 * DAY)

    def status(self, run_id: str) -> str | None:
        return self.r.get(f"{self.p}status:{run_id}")

    def add_event(self, run_id: str, event: dict[str, Any]) -> int:
        """Append an event; returns its sequence number (0, 1, 2 ...)."""
        key = f"{self.p}events:{run_id}"
        # The sequence number is the list position, so it is the same for every reader.
        seq = int(self.r.llen(key))
        event["seq"] = seq
        pos = int(self.r.rpush(key, json.dumps(event, default=str))) - 1
        if pos != seq:  # another writer got in between (should not happen: one worker per run)
            event["seq"] = pos
            self.r.lset(key, pos, json.dumps(event, default=str))
        self.r.expire(key, DAY)
        return pos

    def events(self, run_id: str, since: int = 0) -> list[dict[str, Any]]:
        return [json.loads(e) for e in self.r.lrange(f"{self.p}events:{run_id}", since, -1)]

    def known(self, run_id: str) -> bool:
        return bool(self.r.exists(f"{self.p}status:{run_id}"))


def make_queue() -> RunQueue | None:
    """The queue when RUN_QUEUE=redis, else None (the web server builds runs itself)."""
    from agent.config import settings

    if settings.run_queue != "redis":
        return None
    if not settings.redis_url:
        raise RuntimeError("RUN_QUEUE=redis needs REDIS_URL")
    return RunQueue(settings.redis_url)
