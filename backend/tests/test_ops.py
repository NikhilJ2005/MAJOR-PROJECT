"""Production plumbing: resume after a restart, metrics, rate limits, JSON logs, request ids, headers."""

import json
import logging
import time
import uuid

from fastapi.testclient import TestClient

from agent import llm as llm_mod
from agent.fake_llm import FakeLLM
from agent.graph import initial_state
from app.ops import JsonFormatter, RateLimiter, metrics_text


def test_an_interrupted_build_resumes_from_its_checkpoint():
    """A redeploy stops a build after the Planner; on startup it continues and finishes."""
    from app.main import manager, store

    llm_mod.set_llm(FakeLLM())
    try:
        user_id = store.add_user(f"r-{uuid.uuid4().hex[:6]}@example.com", "x")
        state = initial_state("A blog with posts and comments", {"codegen_mode": "llm"})
        store.add_run(state["run_id"], user_id, state["prompt"], "fastapi")
        config = {"configurable": {"thread_id": state["run_id"]}}
        for update in manager.graph.stream(state, config, stream_mode="updates"):
            if "plan" in update:
                break  # the server "dies" here
        assert manager.graph.get_state(config).next == ("generate",)

        assert state["run_id"] in manager.recover()
        run = manager.runs[state["run_id"]]
        run.worker.join(timeout=120)
        assert run.status == "succeeded"
        assert store.run(state["run_id"])["status"] == "succeeded"
        assert any(e.get("text") == "Resumed after a server restart" for e in run.events)
    finally:
        llm_mod.set_llm(None)


def test_metrics_need_a_token_and_use_prometheus_format(monkeypatch):
    from app.main import app

    monkeypatch.setenv("METRICS_TOKEN", "m-secret")
    with TestClient(app) as client:
        assert client.get("/api/metrics").status_code == 401
        r = client.get("/api/metrics", headers={"Authorization": "Bearer m-secret"})
        assert r.status_code == 200
        assert "# TYPE vibestack_runs_total counter" in r.text and "vibestack_active_runs" in r.text


def test_metrics_text_adds_up_runs():
    rows = [
        {"status": "succeeded", "created_at": 0, "finished_at": 30, "repairs": 1, "bugs_found": 2, "bugs_fixed": 2, "tokens": 100, "cost_usd": 0.5},
        {"status": "failed", "created_at": 0, "finished_at": 10, "repairs": 3, "bugs_found": 1, "bugs_fixed": 0, "tokens": 50, "cost_usd": 0.25},
    ]
    text = metrics_text(rows, active_runs=1, previews=0)
    assert 'vibestack_runs_total{status="failed"} 1' in text
    assert "vibestack_build_seconds_total 40" in text and "vibestack_llm_cost_usd_total 0.75" in text


def test_rate_limiter_allows_a_burst_then_refuses():
    limiter = RateLimiter(limit=3, seconds=60)
    assert [limiter.allow("1.2.3.4") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("5.6.7.8")  # per address


def test_login_attempts_are_rate_limited(monkeypatch):
    from app import main

    monkeypatch.setattr(main, "auth_limit", RateLimiter(limit=2, seconds=60))
    with TestClient(main.app) as client:
        creds = {"email": "nobody@example.com", "password": "wrong-password"}
        codes = [client.post("/api/auth/login", json=creds).status_code for _ in range(3)]
    assert codes == [401, 401, 429]


def test_responses_carry_a_request_id_and_security_headers():
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/health", headers={"X-Request-ID": "abc123"})
        assert r.headers["x-request-id"] == "abc123"
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
        big = client.post("/api/auth/login", content=b"x" * 2_000_001, headers={"content-type": "application/json"})
        assert big.status_code == 413


def test_logs_are_json_lines():
    record = logging.LogRecord("vibestack", logging.INFO, __file__, 1, "built %s", ("blog",), None)
    record.created = time.time()
    line = json.loads(JsonFormatter().format(record))
    assert line["message"] == "built blog" and line["level"] == "info" and "request_id" in line
