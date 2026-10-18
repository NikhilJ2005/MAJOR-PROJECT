"""Platform robustness: readiness, back-pressure, idempotent starts, shared rate limits, retention."""

import dataclasses
import time
import uuid

import fakeredis
from fastapi.testclient import TestClient

from app.ops import Idempotency, RateLimiter


def _login(client):
    r = client.post("/api/auth/register", json={"email": f"r-{uuid.uuid4().hex[:8]}@example.com", "password": "long-enough-pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_ready_checks_the_database():
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/ready")
        assert r.status_code == 200 and r.json()["database"] == "ok"


def test_the_same_request_twice_starts_one_build(fake_llm, monkeypatch):
    from app.main import app

    with TestClient(app) as client:
        me = _login(client)
        body = {"prompt": "A blog with posts and comments", "review_design": True}
        first = client.post("/api/runs", json=body, headers={**me, "Idempotency-Key": "abc"}).json()
        again = client.post("/api/runs", json=body, headers={**me, "Idempotency-Key": "abc"}).json()
        assert first == again


def test_a_user_cannot_flood_the_builders(fake_llm, monkeypatch):
    from app import main
    from app.main import app

    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, max_active_runs_per_user=1))
    with TestClient(app) as client:
        me = _login(client)
        body = {"prompt": "A blog with posts and comments", "review_design": True}  # stays running (waits for approval)
        assert client.post("/api/runs", json=body, headers=me).status_code == 201
        r = client.post("/api/runs", json=body, headers=me)
        assert r.status_code == 429 and "builds running" in r.json()["detail"]


def test_rate_limits_and_idempotency_are_shared_through_redis():
    redis = fakeredis.FakeRedis(decode_responses=True)
    a, b = RateLimiter(2, 60, redis=redis), RateLimiter(2, 60, redis=redis)  # two web servers
    assert a.allow("1.2.3.4") and b.allow("1.2.3.4")
    assert not a.allow("1.2.3.4")
    one, two = Idempotency(redis), Idempotency(redis)
    one.put("u:k", {"run_id": "x"})
    assert two.get("u:k") == {"run_id": "x"}


def test_retention_keeps_the_current_version(fake_llm):
    from app.main import app, manager, store

    with TestClient(app) as client:
        me = _login(client)
        r = client.post("/api/projects", json={"prompt": "A blog with posts and comments"}, headers=me).json()
        for _ in range(200):
            if (store.run(r["run_id"]) or {}).get("finished_at"):
                break
            time.sleep(0.1)
        old = f"old-{uuid.uuid4().hex[:6]}"
        store.add_run(old, store.run(r["run_id"])["owner_id"], "an old attempt", "fastapi", r["project_id"])
        store.finish_run(old, "failed", {})
        store._write("UPDATE runs SET finished_at = 1 WHERE id = :id", id=old)
        manager.prune(30)
        assert store.run(old)["status"] == "pruned"
        assert store.run(r["run_id"])["status"] != "pruned"  # the project's current version stays
        assert client.get(f"/api/runs/{old}", headers=me).json()["status"] == "pruned"
