"""Lovable-style projects: build version 1, edit it with a follow-up prompt, restore an older version."""

import json
import time
import uuid

from fastapi.testclient import TestClient

from agent import llm as llm_mod
from agent.fake_llm import BLOG_SPEC, FakeLLM

WITH_TAGS = json.loads(json.dumps(BLOG_SPEC))
WITH_TAGS["entities"].append({"name": "Tag", "fields": [{"name": "label", "unique": True}, {"name": "post_id", "references": "Post"}]})


def _login(client):
    r = client.post("/api/auth/register", json={"email": f"p-{uuid.uuid4().hex[:8]}@example.com", "password": "long-enough-pw"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _wait(client, run_id, headers, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = client.get(f"/api/runs/{run_id}", headers=headers).json()
        if snap["status"] in ("succeeded", "failed", "error"):
            return snap
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def test_project_build_edit_restore():
    from app.main import app

    fake = FakeLLM(edit_spec=WITH_TAGS)
    llm_mod.set_llm(fake)
    try:
        with TestClient(app) as client:
            me = _login(client)
            r = client.post("/api/projects", json={"prompt": "A blog with posts and comments", "stack": "flask"}, headers=me)
            assert r.status_code == 201, r.text
            project_id, v1 = r.json()["project_id"], r.json()["run_id"]
            snap1 = _wait(client, v1, me)
            assert snap1["status"] == "succeeded" and snap1["version"] == 1

            project = client.get(f"/api/projects/{project_id}", headers=me).json()
            assert project["current_run_id"] == v1 and project["name"] == "Blog Api"

            r = client.post(f"/api/projects/{project_id}/messages", json={"prompt": "add tags to posts"}, headers=me)
            assert r.status_code == 201, r.text
            v2 = r.json()["run_id"]
            snap2 = _wait(client, v2, me)
            assert snap2["status"] == "succeeded", snap2.get("report")
            assert snap2["version"] == 2 and snap2["base_run_id"] == v1
            assert snap2["changes"] == {"added": ["Tag"], "changed": [], "removed": []}
            assert "app/routers/tag.py" in snap2["files"]
            assert any("kept Post, Comment" in e["action"] for e in snap2["ledger"])

            project = client.get(f"/api/projects/{project_id}", headers=me).json()
            assert [v["version"] for v in project["versions"]] == [1, 2]
            assert project["current_run_id"] == v2

            assert client.post(f"/api/projects/{project_id}/current", json={"run_id": v1}, headers=me).status_code == 200
            assert client.get(f"/api/projects/{project_id}", headers=me).json()["current_run_id"] == v1

            other = _login(client)
            assert client.get(f"/api/projects/{project_id}", headers=other).status_code == 404
            assert client.post(f"/api/projects/{project_id}/messages", json={"prompt": "hijack"}, headers=other).status_code == 404
            listing = client.get("/api/projects", headers=me).json()
            assert listing[0]["id"] == project_id and listing[0]["versions"] == 2
    finally:
        llm_mod.set_llm(None)


def test_a_failed_build_can_be_edited_or_retried(monkeypatch):
    """Edits are not blocked by a failed version: the next version rebuilds every entity."""
    from agent import nodes
    from agent.sandbox import GateResult, LocalSandbox
    from app.main import app

    real = LocalSandbox()
    broken = {"on": True}

    class Sandbox:
        def validate(self, files, stack="fastapi"):
            if broken["on"]:
                return GateResult(False, "tests", "FAILED tests/test_smoke.py::test_health - assert 500 == 200\n1 failed", 0.1)
            return real.validate(files, stack)

        def make_migrations(self, files, stack):
            return real.make_migrations(files, stack)

    monkeypatch.setattr(nodes, "get_sandbox", lambda: Sandbox())
    llm_mod.set_llm(FakeLLM())
    try:
        with TestClient(app) as client:
            me = _login(client)
            r = client.post("/api/projects", json={"prompt": "A blog with posts and comments", "stack": "fastapi"}, headers=me)
            project_id, v1 = r.json()["project_id"], r.json()["run_id"]
            assert _wait(client, v1, me)["status"] == "failed"

            broken["on"] = False
            r = client.post(f"/api/projects/{project_id}/messages", json={"prompt": "Try again: rebuild this app and fix whatever stopped it."}, headers=me)
            assert r.status_code == 201, r.text
            snap = _wait(client, r.json()["run_id"], me)
            assert snap["status"] == "succeeded" and snap["version"] == 2 and snap["base_run_id"] == v1
            assert any("every entity is rebuilt" in e["action"] for e in snap["ledger"])
            assert client.get(f"/api/projects/{project_id}", headers=me).json()["current_run_id"] == snap["run_id"]
    finally:
        llm_mod.set_llm(None)
