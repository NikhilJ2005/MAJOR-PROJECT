"""Built-in blueprints build with verified code only, and pattern actions behave as designed."""

import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from agent import blueprints
from agent.codegen import render_project
from agent.state import ProjectSpec


def test_every_blueprint_is_a_valid_design_with_pattern_actions_only():
    assert len(blueprints.all_blueprints()) >= 5
    for bp in blueprints.all_blueprints():
        spec = ProjectSpec.model_validate(bp["spec"])
        assert spec.actions and all(a.kind != "custom" for a in spec.actions), bp["id"]
        assert all(e.sample_rows for e in spec.entities), bp["id"]


def test_the_closest_blueprint_is_found_from_the_prompt():
    assert blueprints.closest("Build me a library management system with loans")["id"] == "library"
    assert blueprints.closest("freelance marketplace with escrow wallets")["id"] == "escrow"
    assert blueprints.closest("a recipe sharing site") is None


def test_a_blueprint_builds_through_the_api_without_repairs(offline):
    from app.main import app

    with TestClient(app) as client:
        r = client.post("/api/auth/register", json={"email": f"b-{uuid.uuid4().hex[:8]}@example.com", "password": "long-enough-pw"})
        me = {"Authorization": f"Bearer {r.json()['token']}"}
        assert any(b["id"] == "library" for b in client.get("/api/blueprints").json())
        body = {"prompt": blueprints.get("library")["prompt"], "blueprint": "library", "stack": "flask"}
        run_id = client.post("/api/runs", json=body, headers=me).json()["run_id"]
        deadline = time.time() + 180
        while time.time() < deadline:
            snap = client.get(f"/api/runs/{run_id}", headers=me).json()
            if snap["status"] not in ("running", "awaiting_approval"):
                break
            time.sleep(0.3)
        assert snap["status"] == "succeeded", snap.get("stop_reason")
        assert snap["iteration"] == 0  # first time, no self-healing


@pytest.mark.parametrize("stack", ["fastapi", "django"])
def test_pattern_actions_check_inputs_and_rules(stack):
    from app.preview import PreviewManager

    spec = ProjectSpec.model_validate({**blueprints.get("escrow")["spec"], "stack": stack})
    previews = PreviewManager()
    try:
        p = previews.start(f"bp-{stack}", render_project(spec), stack)
        http = httpx.Client(base_url=p.base_url, trust_env=False, timeout=10)
        creds = {"email": "admin@vibestack.dev", "password": "demo-pass-123"}  # the demo account owns the demo rows
        token = http.post("/auth/login", json=creds).json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}
        wallet = http.get("/wallets/", headers=h).json()[0]
        assert http.post(f"/wallets/{wallet['id']}/deposit", json={}, headers=h).status_code == 422  # amount missing
        assert http.post(f"/wallets/{wallet['id']}/deposit", json={"amount": -5}, headers=h).status_code == 422
        r = http.post(f"/wallets/{wallet['id']}/deposit", json={"amount": 100}, headers=h)
        assert r.status_code == 200 and r.json()["available_balance"] == wallet["available_balance"] + 100
        too_much = r.json()["available_balance"] + 1
        assert http.post(f"/wallets/{wallet['id']}/withdraw", json={"amount": too_much}, headers=h).status_code == 409
        r = http.post(f"/wallets/{wallet['id']}/lock-escrow", json={"amount": 50}, headers=h)
        assert r.status_code == 200 and r.json()["escrow_balance"] == wallet["escrow_balance"] + 50
        bid = next(b for b in http.get("/bids/", headers=h).json() if b["status"] == "pending")
        assert http.post(f"/bids/{bid['id']}/accept", headers=h).json()["status"] == "accepted"
        assert http.post(f"/bids/{bid['id']}/accept", headers=h).status_code == 409  # already accepted
        assert http.post(f"/bids/{bid['id']}/withdraw-bid", headers=h).status_code == 409  # transitions forbid it
    finally:
        previews.stop_all()
