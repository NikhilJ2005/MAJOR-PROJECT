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
        r = client.post("/api/runs", json={"prompt": "A blog with posts and comments"})
        assert r.status_code == 201, r.text
        run_id = r.json()["run_id"]

        snap = _wait(client, run_id, {"succeeded", "failed", "error"})
        assert snap["status"] == "succeeded", snap
        assert snap["spec"]["project_name"] == "blog_api"
        assert snap["iteration"] == 1  # inject_fault defaults to on: the demo always shows a heal
        assert snap["file_plan"]

        with client.stream("GET", f"/api/runs/{run_id}/events") as resp:
            body = "".join(resp.iter_text())
        assert '"node": "parse_spec"' in body and '"node": "reflect"' in body
        with client.stream("GET", f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "3"}) as resp:
            assert '"node": "parse_spec"' not in "".join(resp.iter_text())

        assert "APIRouter" in client.get(f"/api/runs/{run_id}/files/app/routers/post.py").text
        z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/runs/{run_id}/download").content))
        assert "blog_api/VIBESTACK_LEDGER.md" in z.namelist()


def test_short_prompt_rejected(fake_llm):
    from app.main import app

    with TestClient(app) as client:
        assert client.post("/api/runs", json={"prompt": "hi"}).status_code == 422
