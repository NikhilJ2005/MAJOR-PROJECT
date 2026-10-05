import io
import time
import zipfile

from fastapi.testclient import TestClient


def _wait(client, run_id, statuses, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = client.get(f"/api/runs/{run_id}").json()
        if snap["status"] in statuses:
            return snap
        time.sleep(0.2)
    raise AssertionError(f"run stuck in {snap['status']}")


def test_end_to_end_over_http(fake_llm):
    from app.main import app

    with TestClient(app) as client:
        assert client.get("/api/config").json()["llm_enabled"] is True
        r = client.post("/api/runs", json={"prompt": "A blog with posts and comments", "inject_fault": True})
        assert r.status_code == 201, r.text
        run_id = r.json()["run_id"]

        snap = _wait(client, run_id, {"awaiting_approval"})
        assert snap["spec"]["project_name"] == "blog_api"

        with client.stream("GET", f"/api/runs/{run_id}/events") as resp:
            body = "".join(resp.iter_text())
        assert "event: interrupt" in body

        assert client.post(f"/api/runs/{run_id}/approve", json={"approved": True}).status_code == 200
        snap = _wait(client, run_id, {"succeeded", "failed", "error"})
        assert snap["status"] == "succeeded", snap
        assert snap["iteration"] == 1

        with client.stream("GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "0"}) as resp:
            body = "".join(resp.iter_text())
        assert "event: node" in body and '"node": "reflect"' in body

        assert "APIRouter" in client.get(f"/api/runs/{run_id}/files/app/routers/post.py").text
        z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/runs/{run_id}/download").content))
        assert "blog_api/VIBESTACK_LEDGER.md" in z.namelist()


def test_invalid_edited_spec_rejected(fake_llm):
    from app.main import app

    with TestClient(app) as client:
        run_id = client.post("/api/runs", json={"prompt": "A blog with posts and comments"}).json()["run_id"]
        _wait(client, run_id, {"awaiting_approval"})
        r = client.post(f"/api/runs/{run_id}/approve", json={"approved": True, "spec": {"project_name": "x", "entities": []}})
        assert r.status_code == 422
