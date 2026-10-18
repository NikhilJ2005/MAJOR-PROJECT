import io
import json
import time
import uuid
import zipfile

from fastapi.testclient import TestClient


def _wait(client, run_id, statuses, headers, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = client.get(f"/api/runs/{run_id}", headers=headers).json()
        if snap["status"] in statuses:
            return snap
        time.sleep(0.2)
    raise AssertionError(f"run stuck in {snap['status']}")


def _login(client, email=None):
    """Register a fresh platform user and return its auth header."""
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/auth/register", json={"email": email, "password": "long-enough-pw"})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_accounts_register_login_me():
    from app.main import app

    with TestClient(app) as client:
        email = f"a-{uuid.uuid4().hex[:8]}@example.com"
        _login(client, email)
        assert client.post("/api/auth/register", json={"email": email, "password": "long-enough-pw"}).status_code == 409
        assert client.post("/api/auth/login", json={"email": email, "password": "wrong-password"}).status_code == 401
        token = client.post("/api/auth/login", json={"email": email, "password": "long-enough-pw"}).json()["token"]
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["email"] == email
        assert client.get("/api/auth/me").status_code == 401
        assert client.post("/api/runs", json={"prompt": "A blog with posts and comments"}).status_code == 401


def test_end_to_end_over_http(fake_llm):
    from app.main import app

    with TestClient(app) as client:
        me = _login(client)
        assert client.get("/api/config").json()["llm_enabled"] is True
        r = client.post("/api/runs", json={"prompt": "A blog with posts and comments", "stack": "flask"}, headers=me)
        assert r.status_code == 201, r.text
        run_id = r.json()["run_id"]

        snap = _wait(client, run_id, {"succeeded", "failed", "error"}, me)
        assert snap["status"] == "succeeded", snap
        assert snap["spec"]["project_name"] == "blog_api"
        assert snap["spec"]["stack"] == "flask"
        assert snap["iteration"] == 0  # no bugs are planted: a clean model output needs no repair
        assert snap["requirements"] and snap["file_plan"]

        token = me["Authorization"][7:]
        with client.stream("GET", f"/api/runs/{run_id}/events?token={token}") as resp:
            body = "".join(resp.iter_text())
        events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        nodes = [e["node"] for e in events if e["type"] == "node"]
        assert nodes[0] == "parse_spec" and nodes[-1] == "package"
        assert any(e["type"] == "progress" and e["node"] == "parse_spec" for e in events)  # live progress
        # Live code: the design and the business logic arrive piece by piece while they are written.
        streamed = {}
        for e in events:
            if e["type"] == "stream":
                streamed[e["label"]] = streamed.get(e["label"], "") + e["delta"]
        assert '"entities"' in streamed["Design (JSON)"]
        assert "def stats" in streamed["app/services/post.py"]
        after = next(e["seq"] for e in events if e["type"] == "node" and e["node"] == "parse_spec")
        with client.stream("GET", f"/api/runs/{run_id}/events?token={token}", headers={"Last-Event-ID": str(after)}) as resp:
            assert '"node": "parse_spec"' not in "".join(resp.iter_text())

        assert "Blueprint" in client.get(f"/api/runs/{run_id}/files/app/routers/post.py", headers=me).text
        z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/runs/{run_id}/download?token={token}").content))
        names = z.namelist()
        assert "blog_api/VIBESTACK_LEDGER.md" in names
        assert any(n.startswith("blog_api/migrations/versions/") for n in names)

        assert [r["run_id"] for r in client.get("/api/runs", headers=me).json()] == [run_id]
        stats = client.get("/api/stats", headers=me).json()["mine"]
        assert stats["runs"] == 1 and stats["succeeded"] == 1

        # Another user can't see, read or download this run.
        other = _login(client)
        assert client.get(f"/api/runs/{run_id}", headers=other).status_code == 404
        assert client.get(f"/api/runs/{run_id}/files/app/main.py", headers=other).status_code == 404
        assert client.get(f"/api/runs/{run_id}/download", headers=other).status_code == 404
        assert client.get("/api/runs", headers=other).json() == []


def test_short_prompt_rejected(fake_llm):
    from app.main import app

    with TestClient(app) as client:
        assert client.post("/api/runs", json={"prompt": "hi"}, headers=_login(client)).status_code == 422


def test_live_preview_runs_generated_app_behind_proxy(fake_llm):
    from app.main import app, previews

    with TestClient(app) as client:
        me = _login(client)
        run_id = client.post("/api/runs", json={"prompt": "A blog with posts and comments"}, headers=me).json()["run_id"]
        assert _wait(client, run_id, {"succeeded", "failed", "error"}, me)["status"] == "succeeded"
        assert client.get(f"/api/runs/{run_id}/preview", headers=me).json()["running"] is False
        assert client.get(f"/preview/{run_id}/health").status_code == 404  # the run id is not the preview key

        assert client.post(f"/api/runs/{run_id}/preview", headers=_login(client)).status_code == 404  # not yours
        r = client.post(f"/api/runs/{run_id}/preview", headers=me)
        assert r.status_code == 200, r.text
        base = r.json()["base"]
        assert run_id not in base  # an unguessable key, not the run id
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
            user = client.get(f"{base}/auth/me", headers=auth).json()
            assert client.post(f"{base}/posts/", json={"title": "x", "body": "y", "author_id": user["id"]}).status_code == 401
            r = client.post(f"{base}/posts/", json={"title": "Hello", "body": "World", "author_id": user["id"]}, headers=auth)
            assert r.status_code == 201, r.text
            assert [p["title"] for p in client.get(f"{base}/posts/?q=Hell").json()] == ["Hello"]
            post_id = r.json()["id"]
            r = client.post(f"{base}/comments/", json={"content": "Nice", "post_id": post_id}, headers=auth)
            assert r.status_code == 201, r.text
            assert len(client.get(f"{base}/comments/?post_id={post_id}").json()) == 1
            assert client.get(f"{base}/comments/?post_id=999").json() == []  # foreign-key filter
            assert client.post(f"{base}/comments/", json={"content": "x", "post_id": 999}, headers=auth).status_code == 409
        finally:
            previews.stop_all()


def test_django_preview_boots_after_migrate(fake_llm):
    from app.main import app, previews

    with TestClient(app) as client:
        me = _login(client)
        run_id = client.post("/api/runs", json={"prompt": "A blog with posts and comments", "stack": "django"},
                             headers=me).json()["run_id"]
        assert _wait(client, run_id, {"succeeded", "failed", "error"}, me)["status"] == "succeeded"
        base = client.post(f"/api/runs/{run_id}/preview", headers=me).json()["base"]
        try:
            assert client.get(f"{base}/health").json() == {"status": "ok"}
            assert "openapi" in client.get(f"{base}/docs").text
            assert client.get(f"{base}/posts/").json() == []
        finally:
            previews.stop_all()


def test_ui_pages_are_revalidated_but_hashed_assets_are_cached(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.main import UIFiles

    (tmp_path / "_next" / "static").mkdir(parents=True)
    (tmp_path / "index.html").write_text("<html>v3</html>")
    (tmp_path / "_next" / "static" / "a1b2.js").write_text("1")
    ui = FastAPI()
    ui.mount("/", UIFiles(directory=tmp_path, html=True))
    client = TestClient(ui)
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert "immutable" in client.get("/_next/static/a1b2.js").headers["cache-control"]
