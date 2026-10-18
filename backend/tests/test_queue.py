"""Redis build queue: the web server queues runs, a worker process builds them (fakeredis, no server needed)."""

import json
import time
import uuid

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app.queue import RunQueue
from app.runs import RunManager
from app.worker import Worker


@pytest.fixture
def queue():
    return RunQueue(client=fakeredis.FakeRedis(decode_responses=True), prefix=f"t{uuid.uuid4().hex[:6]}:")


@pytest.fixture
def queued_web(queue):
    """The web app's run manager switched to queue mode for one test."""
    from app.main import manager

    saved = manager.queue, manager.executes
    manager.queue, manager.executes = queue, False
    yield manager
    manager.queue, manager.executes = saved


def _login(client):
    r = client.post("/api/auth/register", json={"email": f"q-{uuid.uuid4().hex[:8]}@example.com", "password": "long-enough-pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_events_keep_their_order_and_status_is_shared(queue):
    assert [queue.add_event("r1", {"n": i}) for i in range(3)] == [0, 1, 2]
    assert [e["n"] for e in queue.events("r1", 1)] == [1, 2]
    queue.set_status("r1", "running")
    assert queue.status("r1") == "running" and queue.known("r1")


def test_only_one_worker_claims_a_run(queue):
    assert queue.claim("r1", "worker-a")
    assert not queue.claim("r1", "worker-b")
    queue.release("r1")
    assert queue.claim("r1", "worker-b")


def test_web_queues_and_a_worker_builds(fake_llm, queue, queued_web):
    from app.main import app, store

    with TestClient(app) as client:
        me = _login(client)
        r = client.post("/api/runs", json={"prompt": "A blog with posts and comments", "stack": "fastapi"}, headers=me)
        assert r.status_code == 201, r.text
        run_id = r.json()["run_id"]
        snap = client.get(f"/api/runs/{run_id}", headers=me).json()
        assert snap["status"] == "running" and snap["queued"]
        assert queue.waiting() == 1

        worker = Worker(RunManager(store, queue, executes=True), queue, concurrency=1, name="test-worker")
        assert worker.tick(pop_timeout=1) == run_id
        worker.building[run_id].join(timeout=180)

        snap = client.get(f"/api/runs/{run_id}", headers=me).json()
        assert snap["status"] == "succeeded", snap
        assert not queue.claimed(run_id)  # released when done
        # The browser's event stream (served by the web process) shows what the worker did.
        token = me["Authorization"][7:]
        with client.stream("GET", f"/api/runs/{run_id}/events?token={token}") as resp:
            events = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]
        kinds = {e["type"] for e in events}
        assert {"status", "node", "progress"} <= kinds
        assert events[-1] == {**events[-1], "type": "status", "status": "succeeded"}
        assert [e["seq"] for e in events] == list(range(len(events)))


def test_a_dead_workers_run_is_queued_again(queue):
    from app.main import store

    run_id = f"orphan-{uuid.uuid4().hex[:6]}"
    store.add_run(run_id, 1, "a blog", "fastapi")
    manager = RunManager(store, queue, executes=True)
    queue.set_status(run_id, "running")  # its worker died: no claim is left
    assert run_id in manager.requeue_orphans()
    assert run_id not in manager.requeue_orphans()  # already waiting
    job = queue.pop(timeout=1)
    assert job == {"run_id": run_id, "input": None}
    # It never reached a checkpoint, so there is nothing to continue: it ends as an error, not stuck.
    assert manager.execute_job(job, "w")
    assert store.run(run_id)["status"] == "error"
    assert not manager.execute_job(job, "w")  # finished runs are not built twice


def test_worker_drains_on_stop(queue):
    from app.main import store

    worker = Worker(RunManager(store, queue, executes=True), queue, concurrency=1)
    worker.stopping.set()
    started = time.monotonic()
    worker.serve(drain_seconds=5)
    assert time.monotonic() - started < 2
