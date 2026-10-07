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


def test_live_preview_runs_generated_app_behind_proxy(fake_llm):
    from app.main import app, previews

    with TestClient(app) as client:
        run_id = client.post("/api/runs", json={"prompt": "A blog with posts and comments", "fault": "none"}).json()["run_id"]
        assert _wait(client, run_id, {"succeeded", "failed", "error"})["status"] == "succeeded"
        assert client.get(f"/api/runs/{run_id}/preview").json()["running"] is False
        assert client.get(f"/preview/{run_id}/health").status_code == 404

        r = client.post(f"/api/runs/{run_id}/preview")
        assert r.status_code == 200, r.text
        base = r.json()["base"]
        try:
            assert client.get(f"{base}/health").json() == {"status": "ok"}

            # Swagger UI works behind the proxy: it points at the prefixed OpenAPI document.
            docs = client.get(f"{base}/docs").text
            assert f"{base}/openapi.json" in docs
            assert "/posts/" in client.get(f"{base}/openapi.json").json()["paths"]

            # Use the generated app like a user would: register, log in, write, read.
            creds = {"email": "demo@example.com", "password": "demo-pass-123"}
            assert client.post(f"{base}/auth/register", json=creds).status_code == 201
            token = client.post(f"{base}/auth/login", json=creds).json()["access_token"]
            auth = {"Authorization": f"Bearer {token}"}
            me = client.get(f"{base}/auth/me", headers=auth).json()
            assert client.post(f"{base}/posts/", json={"title": "x", "body": "y", "author_id": me["id"]}).status_code == 401
            r = client.post(f"{base}/posts/", json={"title": "Hello", "body": "World", "author_id": me["id"]}, headers=auth)
            assert r.status_code == 201, r.text
            assert [p["title"] for p in client.get(f"{base}/posts/?q=Hell").json()] == ["Hello"]
        finally:
            previews.stop_all()
