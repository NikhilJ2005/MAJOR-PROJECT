"""GitHub: "Sign in with GitHub" (OAuth) and pushing a generated app to the user's own repository.

Plain HTTP calls to the GitHub REST API (no SDK). A push is four calls, whatever the number of files:

    1. create the repository if it does not exist yet
    2. one tree with every file (contents inline)
    3. one commit on top of the branch
    4. move the branch to that commit

Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET (a GitHub OAuth App) to turn it on. The `workflow`
scope is needed because every generated app ships .github/workflows/ci.yml.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

API = "https://api.github.com"
SCOPES = "repo workflow read:user user:email"
TIMEOUT_S = 30

# Tests swap this for an httpx.MockTransport; None means the real network.
transport: httpx.BaseTransport | None = None


class GitHubError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(os.environ.get("GITHUB_CLIENT_ID") and os.environ.get("GITHUB_CLIENT_SECRET"))


def _http() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT_S, transport=transport)


def _api(token: str, method: str, path: str, json: Any = None, ok: tuple[int, ...] = (200, 201)) -> Any:
    """One REST call; raises GitHubError with GitHub's message when the status is not in `ok`."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    with _http() as http:
        r = http.request(method, f"{API}{path}", json=json, headers=headers)
    if r.status_code not in ok:
        detail = r.json().get("message", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise GitHubError(f"GitHub {method} {path} -> {r.status_code}: {str(detail)[:200]}")
    return r.json() if r.content else None


# --------------------------------------------------------------------------- OAuth


def authorize_url(state: str, redirect_uri: str) -> str:
    query = httpx.QueryParams(client_id=os.environ["GITHUB_CLIENT_ID"], redirect_uri=redirect_uri, scope=SCOPES,
                              state=state, allow_signup="true")
    return f"https://github.com/login/oauth/authorize?{query}"


def exchange_code(code: str, redirect_uri: str) -> str:
    """The OAuth code from the callback -> an access token."""
    with _http() as http:
        r = http.post("https://github.com/login/oauth/access_token", headers={"Accept": "application/json"}, data={
            "client_id": os.environ["GITHUB_CLIENT_ID"], "client_secret": os.environ["GITHUB_CLIENT_SECRET"],
            "code": code, "redirect_uri": redirect_uri,
        })
    token = r.json().get("access_token") if r.status_code == 200 else None
    if not token:
        raise GitHubError(f"GitHub did not return a token: {r.text[:200]}")
    return token


def profile(token: str) -> dict[str, str]:
    """The user's login and primary verified email."""
    user = _api(token, "GET", "/user")
    email = user.get("email")
    if not email:
        emails = _api(token, "GET", "/user/emails")
        email = next((e["email"] for e in emails if e.get("primary") and e.get("verified")), None)
    if not email:
        raise GitHubError("your GitHub account has no verified email address")
    return {"login": user["login"], "email": email.lower()}


# --------------------------------------------------------------------------- push


def repo_name(text: str) -> str:
    """A safe repository name, e.g. 'Blog Api' -> 'blog-api'."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:90] or "vibestack-app"


def push(token: str, owner: str, name: str, files: dict[str, str], message: str, private: bool = True) -> dict[str, str]:
    """Commit `files` as the whole content of the repository (created when missing). Returns its URL and commit."""
    full = f"{owner}/{name}"
    try:
        repo = _api(token, "GET", f"/repos/{full}")
    except GitHubError as exc:
        if "-> 404" not in str(exc):
            raise
        repo = _api(token, "POST", "/user/repos", {"name": name, "private": private, "auto_init": True,
                                                   "description": "Built, tested and self-healed by VibeStack"})
    branch = repo.get("default_branch") or "main"
    head = _api(token, "GET", f"/repos/{full}/git/ref/heads/{branch}")["object"]["sha"]
    tree = _api(token, "POST", f"/repos/{full}/git/trees", {"tree": [
        {"path": path, "mode": "100644", "type": "blob", "content": content} for path, content in sorted(files.items())
    ]})
    commit = _api(token, "POST", f"/repos/{full}/git/commits", {"message": message, "tree": tree["sha"], "parents": [head]})
    _api(token, "PATCH", f"/repos/{full}/git/refs/heads/{branch}", {"sha": commit["sha"]})
    return {"repo": full, "url": repo["html_url"], "commit": commit["sha"], "branch": branch}


def ci_status(token: str, full: str) -> dict[str, Any] | None:
    """The latest GitHub Actions run: status (queued/in_progress/completed), conclusion and link."""
    runs = _api(token, "GET", f"/repos/{full}/actions/runs?per_page=1").get("workflow_runs") or []
    if not runs:
        return None
    run = runs[0]
    return {"status": run["status"], "conclusion": run.get("conclusion"), "url": run["html_url"], "commit": run["head_sha"]}


def with_badge(readme: str, full: str) -> str:
    """The generated README with a CI badge for the pushed repository on top."""
    badge = f"[![CI](https://github.com/{full}/actions/workflows/ci.yml/badge.svg)](https://github.com/{full}/actions)\n\n"
    return readme if badge in readme else badge + readme
