"""One-click deploy to Kubernetes, against a fake kubectl (it records every call) and a fake live app."""

import dataclasses
import io
import json
import stat
import sys
import textwrap
import zipfile

import httpx
import pytest
import yaml

from agent.config import settings
from agent.state import ProjectSpec
from app import deploy
from app.store import Store
from tests.specs import ESCROW

FAKE_KUBECTL = textwrap.dedent("""\
    #!{python}
    import json, os, sys
    log = os.environ.get("FAKE_LOG") or "{log}"
    stdin = sys.stdin.read() if "-f" in sys.argv else ""
    with open(log, "a") as f:
        f.write(json.dumps({{"args": sys.argv[1:], "stdin": stdin}}) + "\\n")
    fail = "{fail}"
    if fail and fail in " ".join(sys.argv[1:]):
        print("Back-off restarting failed container: CrashLoopBackOff")
        sys.exit(1)
    print("ok")
""")


@pytest.fixture
def cluster(tmp_path, monkeypatch):
    """Deploy settings pointing at a fake kubectl; returns a function that builds a Deployer."""
    monkeypatch.setattr(deploy, "settings", dataclasses.replace(
        settings, deploy_domain="apps.test", deploy_runtime_image="ghcr.io/x/runtime:1",
        public_url="https://vibestack.test", deploy_kubeconfig_b64="YXBpVmVyc2lvbjogdjEK"))
    store = Store(f"sqlite:///{tmp_path}/platform.db")
    owner = store.add_user("owner@example.com", "x")
    project = store.project(store.add_project(owner, "escrow", "fastapi"), owner)
    spec = ProjectSpec.model_validate(ESCROW).model_dump()
    files = {"app/main.py": "print('hi')", "tests/test_smoke.py": "secret tests"}
    log = tmp_path / "kubectl.log"

    def make(fail: str = "", live: bool = True) -> tuple[deploy.Deployer, dict]:
        script = tmp_path / "kubectl"
        script.write_text(FAKE_KUBECTL.format(python=sys.executable, log=log, fail=fail))
        script.chmod(script.stat().st_mode | stat.S_IEXEC)

        def app(request: httpx.Request) -> httpx.Response:  # the deployed app, as seen through its public URL
            if not live:
                return httpx.Response(503)
            if request.url.path == "/auth/login":
                return httpx.Response(200, json={"access_token": "t"})
            return httpx.Response(201 if request.url.path == "/auth/register" else 200, json=[])

        d = deploy.Deployer(store, files=lambda run_id: files, spec=lambda run_id: spec,
                            kubectl=deploy.Kubectl(binary=str(script)), http=httpx.Client(transport=httpx.MockTransport(app)))
        return d, project

    make.store, make.log, make.owner = store, log, owner
    return make


def _calls(log):
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_deploy_applies_everything_and_tests_the_live_url(cluster):
    deployer, project = cluster()
    row = deployer.start(project, "run12345abcd", cluster.owner, wait=True)
    assert row["status"] == "live", row["message"]
    assert row["url"] == "http://escrow-market-" + project["id"][:6] + ".apps.test"
    steps = json.loads(row["steps"])
    assert [s["state"] for s in steps] == ["done"] * 5
    assert "sign-up and login work" in steps[-1]["detail"]

    calls = _calls(cluster.log)
    applied = list(yaml.safe_load_all(next(c["stdin"] for c in calls if c["args"][0] == "apply")))
    kinds = [o["kind"] for o in applied]
    assert kinds[:4] == ["Namespace", "ResourceQuota", "LimitRange", "Secret"]
    assert {"StatefulSet", "Job", "Deployment", "Service", "Ingress", "NetworkPolicy"} <= set(kinds)
    app = next(o for o in applied if o["kind"] == "Deployment")
    init = app["spec"]["template"]["spec"]["initContainers"][0]
    code_url = init["env"][0]["value"]
    assert code_url.startswith(f"https://vibestack.test/api/deployments/{project['id']}/code?token=")
    secret = next(o for o in applied if o["kind"] == "Secret")["stringData"]
    assert secret["ADMIN_EMAILS"] == "owner@example.com" and "@escrow-market-db:5432/app" in secret["DATABASE_URL"]
    assert any(c["args"][2:4] == ["wait", "--for=condition=complete"] for c in calls)  # migrations before the app

    # The pods fetch the code with the secret token only; tests are left out.
    token = code_url.split("token=")[1]
    assert deployer.code_zip(project["id"], "wrong") is None
    names = zipfile.ZipFile(io.BytesIO(deployer.code_zip(project["id"], token))).namelist()
    assert names == ["app/main.py"]


def test_redeploy_keeps_the_database_password_and_token(cluster):
    deployer, project = cluster()
    first = deployer.start(project, "run1aaaaaaaa", cluster.owner, wait=True)
    second = deployer.start(project, "run2bbbbbbbb", cluster.owner, wait=True)
    assert first["token"] == second["token"] and second["run_id"] == "run2bbbbbbbb"
    secrets = [next(o for o in yaml.safe_load_all(c["stdin"]) if o["kind"] == "Secret")["stringData"]
               for c in _calls(cluster.log) if c["args"][0] == "apply"]
    assert secrets[0]["POSTGRES_PASSWORD"] == secrets[1]["POSTGRES_PASSWORD"]


def test_a_failed_rollout_is_explained(cluster):
    deployer, project = cluster(fail="deployment/")
    row = deployer.start(project, "run12345abcd", cluster.owner, wait=True)
    assert row["status"] == "failed"
    assert "crashes when it starts" in row["message"]  # recognised from the cluster's events
    steps = {s["id"]: s["state"] for s in json.loads(row["steps"])}
    assert steps["migrate"] == "done" and steps["rollout"] == "failed" and steps["smoke"] == "waiting"


def test_undeploy_deletes_the_namespace_and_limits_apply(cluster, monkeypatch):
    deployer, project = cluster()
    deployer.start(project, "run12345abcd", cluster.owner, wait=True)
    deployer.undeploy(project["id"])
    assert cluster.store.deployment(project["id"]) is None
    assert any(c["args"][:2] == ["delete", "namespace"] for c in _calls(cluster.log))
    monkeypatch.setattr(deploy, "settings", dataclasses.replace(deploy.settings, deploy_max_per_user=0))
    with pytest.raises(ValueError, match="apps live at a time"):
        deployer.start(project, "run12345abcd", cluster.owner)


def test_deploy_is_off_until_configured(monkeypatch):
    monkeypatch.setattr(deploy, "settings", dataclasses.replace(settings, deploy_domain=""))
    assert not deploy.enabled()


def test_runtime_image_has_every_package_generated_apps_use():
    from pathlib import Path

    from agent.stacks import STACKS

    dockerfile = (Path(__file__).resolve().parents[2] / "deploy" / "runtime" / "Dockerfile").read_text()
    spec = ProjectSpec.model_validate({**ESCROW})  # auth + rate limits: the most packages
    for stack in STACKS.values():
        for dep in stack.dependencies(spec):
            assert f'"{dep}"' in dockerfile, f"deploy/runtime/Dockerfile misses {dep}"
