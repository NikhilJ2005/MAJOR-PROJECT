"""Sign in with GitHub and push a project, against a fake GitHub (httpx.MockTransport)."""

import json
import time
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi.testclient import TestClient

from agent import llm as llm_mod
from agent.fake_llm import FakeLLM
from app import github


class FakeGitHub:
    """Just enough of the GitHub API: OAuth, user, repos, trees, commits, refs and Actions runs."""

    def __init__(self, email: str) -> None:
        self.email = email
        self.repos: dict[str, dict] = {}
        self.commits: dict[str, dict] = {}
        self.trees: dict[str, list] = {}
        self.calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append(f"{method} {path}")
        body = json.loads(request.content) if request.content and method != "GET" and "oauth" not in path else {}
        if path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": "gho_test"})
        if request.headers.get("Authorization") != "Bearer gho_test":
            return httpx.Response(401, json={"message": "Bad credentials"})
        if path == "/user":
            return httpx.Response(200, json={"login": "octo", "email": self.email})
        if path == "/user/repos" and method == "POST":
            full = f"octo/{body['name']}"
            self.commits["c0"] = {"tree": None, "parents": []}
            self.repos[full] = {"html_url": f"https://github.com/{full}", "default_branch": "main", "head": "c0",
                                "private": body["private"]}
            return httpx.Response(201, json=self.repos[full])
        parts = path.split("/")
        if len(parts) >= 4 and parts[1] == "repos":
            full = f"{parts[2]}/{parts[3]}"
            repo = self.repos.get(full)
            if repo is None:
                return httpx.Response(404, json={"message": "Not Found"})
            rest = "/".join(parts[4:])
            if rest == "":
                return httpx.Response(200, json=repo)
            if rest == "git/ref/heads/main":
                return httpx.Response(200, json={"object": {"sha": repo["head"]}})
            if rest == "git/trees":
                sha = f"t{len(self.trees)}"
                self.trees[sha] = body["tree"]
                return httpx.Response(201, json={"sha": sha})
            if rest == "git/commits":
                sha = f"c{len(self.commits)}"
                self.commits[sha] = {**body}
                return httpx.Response(201, json={"sha": sha})
            if rest == "git/refs/heads/main":
                repo["head"] = body["sha"]
                return httpx.Response(200, json={"object": {"sha": body["sha"]}})
            if rest == "actions/runs":
                return httpx.Response(200, json={"workflow_runs": [
                    {"status": "completed", "conclusion": "success", "html_url": f"https://github.com/{full}/actions/runs/1",
                     "head_sha": repo["head"]}]})
        return httpx.Response(404, json={"message": f"fake GitHub has no {method} {path}"})


def _wait(client, run_id, headers):
    for _ in range(600):
        snap = client.get(f"/api/runs/{run_id}", headers=headers).json()
        if snap["status"] in ("succeeded", "failed", "error"):
            return snap
        time.sleep(0.2)
    raise AssertionError("run did not finish")


def test_sign_in_with_github_then_push_two_versions(monkeypatch):
    from app.main import app

    email = f"octo-{uuid.uuid4().hex[:6]}@example.com"
    fake = FakeGitHub(email)
    monkeypatch.setattr(github, "transport", httpx.MockTransport(fake.handler))
    monkeypatch.setenv("GITHUB_CLIENT_ID", "client")
    monkeypatch.setenv("GITHUB_CLIENT_SECRET", "secret")
    llm_mod.set_llm(FakeLLM())
    try:
        with TestClient(app, follow_redirects=False) as client:
            assert client.get("/api/config").json()["github_enabled"] is True
            start = client.get("/api/auth/github/start")
            assert start.status_code == 307
            authorize = urlparse(start.headers["location"])
            query = parse_qs(authorize.query)
            assert authorize.netloc == "github.com" and "workflow" in query["scope"][0]

            done = client.get("/api/auth/github/callback", params={"code": "abc", "state": query["state"][0]})
            login_token = parse_qs(urlparse(done.headers["location"]).query)["login"][0]
            me = {"Authorization": f"Bearer {login_token}"}
            assert client.get("/api/auth/me", headers=me).json()["email"] == email
            assert client.get("/api/auth/github", headers=me).json() == {"enabled": True, "connected": True, "login": "octo"}
            # A forged state is refused.
            bad = client.get("/api/auth/github/callback", params={"code": "abc", "state": "forged"})
            assert "login_error" in bad.headers["location"]

            r = client.post("/api/projects", json={"prompt": "A blog with posts and comments"}, headers=me)
            project_id, v1 = r.json()["project_id"], r.json()["run_id"]
            assert _wait(client, v1, me)["status"] == "succeeded"

            pushed = client.post(f"/api/projects/{project_id}/github", json={}, headers=me)
            assert pushed.status_code == 200, pushed.text
            first = pushed.json()
            assert first["repo"] == "octo/blog-api" and first["version"] == 1
            tree = fake.trees["t0"]
            paths = {e["path"] for e in tree}
            assert ".github/workflows/ci.yml" in paths and "app/main.py" in paths
            readme = next(e["content"] for e in tree if e["path"] == "README.md")
            assert "octo/blog-api/actions/workflows/ci.yml/badge.svg" in readme
            assert fake.repos["octo/blog-api"]["private"] is True

            # Same repo again: no new repository, one more commit on top of the first.
            again = client.post(f"/api/projects/{project_id}/github", json={}, headers=me).json()
            assert again["repo"] == "octo/blog-api"
            assert fake.commits[again["commit"]]["parents"] == [first["commit"]]
            assert fake.calls.count("POST /user/repos") == 1

            status = client.get(f"/api/projects/{project_id}/github", headers=me).json()
            assert status["version"] == 1 and status["ci"]["conclusion"] == "success"
    finally:
        llm_mod.set_llm(None)


def test_push_needs_a_github_connection():
    from app.main import app

    with TestClient(app) as client:
        r = client.post("/api/auth/register", json={"email": f"n-{uuid.uuid4().hex[:6]}@example.com", "password": "long-enough-pw"})
        me = {"Authorization": f"Bearer {r.json()['token']}"}
        assert client.get("/api/auth/github", headers=me).json()["connected"] is False
        assert client.post("/api/projects/nope/github", json={}, headers=me).status_code == 404


def test_tokens_are_encrypted_at_rest():
    from app.accounts import decrypt, encrypt

    sealed = encrypt("gho_secret")
    assert "gho_secret" not in sealed and decrypt(sealed) == "gho_secret"
    assert decrypt("not-a-token") is None
