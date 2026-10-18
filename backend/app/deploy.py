"""One-click deploy: VibeStack puts a generated app live on a Kubernetes cluster, by itself.

No image is built per app (Railway has no Docker daemon): a generic runtime image
(deploy/runtime/Dockerfile, DEPLOY_RUNTIME_IMAGE) downloads the version's code from VibeStack at
start, through a secret link only this deployment knows. Each app gets its own namespace with a
Postgres, a migration Job, two replicas, an Ingress at https://<name>-<id>.<DEPLOY_DOMAIN>, quotas and
a network rule (the manifests come from plugins/kubernetes.py, the same ones the zip ships).

A deploy is a short state machine, recorded step by step so the UI can show it:
    render -> apply -> migrate -> rollout -> smoke test (against the public URL) -> live
When a step fails, the cluster's events and pod logs are read and explained in plain English.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Callable
from typing import Any

import httpx

from agent import plugins
from agent.config import settings
from agent.state import ProjectSpec

from .accounts import _secret
from .store import Store

log = logging.getLogger(__name__)

STEPS = [
    ("render", "Write the Kubernetes manifests"),
    ("apply", "Send them to the cluster"),
    ("migrate", "Create the database tables"),
    ("rollout", "Start the app (2 replicas)"),
    ("smoke", "Test the live URL"),
]
ROLLOUT_SECONDS = 300
SMOKE_SECONDS = 180  # DNS, the ingress and the TLS certificate can take a while for a new host


class DeployError(Exception):
    def __init__(self, step: str, message: str, diagnosis: str = "") -> None:
        super().__init__(message)
        self.step, self.diagnosis = step, diagnosis


def in_cluster() -> bool:
    return bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def enabled() -> bool:
    """Deploys need a cluster, a wildcard domain, the runtime image and a public address for the code link."""
    has_cluster = bool(settings.deploy_kubeconfig_b64) or in_cluster()
    return bool(has_cluster and settings.deploy_domain and settings.deploy_runtime_image and settings.public_url)


def names(project_id: str, spec: ProjectSpec) -> tuple[str, str, str]:
    """(app name, namespace, public host): DNS-safe, unique per project."""
    base = re.sub(r"[^a-z0-9-]+", "-", spec.project_name.lower()).strip("-")[:30].strip("-") or "app"
    short = re.sub(r"[^a-z0-9]", "", project_id.lower())[:6]
    return base, f"vs-{base}-{short}", f"{base}-{short}.{settings.deploy_domain}"


def derived_secret(namespace: str, purpose: str) -> str:
    """Stable per app and purpose (redeploys keep the same database password), never stored anywhere."""
    return hmac.new(_secret().encode(), f"{namespace}:{purpose}".encode(), hashlib.sha256).hexdigest()[:40]


class Kubectl:
    """kubectl, with the cluster from DEPLOY_KUBECONFIG_B64 (or the pod's own account inside a cluster)."""

    def __init__(self, binary: str = "kubectl") -> None:
        self.binary = os.environ.get("KUBECTL", binary)
        self.env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": tempfile.gettempdir()}
        for key in ("KUBERNETES_SERVICE_HOST", "KUBERNETES_SERVICE_PORT"):
            if os.environ.get(key):
                self.env[key] = os.environ[key]
        if settings.deploy_kubeconfig_b64:
            fd, path = tempfile.mkstemp(prefix="vibestack-kubeconfig-")
            with os.fdopen(fd, "wb") as f:
                f.write(base64.b64decode(settings.deploy_kubeconfig_b64))
            os.chmod(path, 0o600)
            self.env["KUBECONFIG"] = path

    def run(self, *args: str, stdin: str | None = None, timeout: int = 120) -> tuple[int, str]:
        try:
            p = subprocess.run([self.binary, *args], input=stdin, capture_output=True, text=True, timeout=timeout,
                               env=self.env, check=False)
        except FileNotFoundError:
            return 127, f"{self.binary} is not installed"
        except subprocess.TimeoutExpired:
            return 124, f"kubectl {' '.join(args[:3])} did not finish in {timeout}s"
        return p.returncode, (p.stdout + p.stderr).strip()

    def apply(self, manifest: str) -> tuple[int, str]:
        return self.run("apply", "--server-side", "--force-conflicts", "--field-manager=vibestack", "-f", "-", stdin=manifest)

    def wait_job(self, namespace: str, job: str, timeout: int) -> tuple[int, str]:
        return self.run("-n", namespace, "wait", "--for=condition=complete", f"job/{job}", f"--timeout={timeout}s",
                        timeout=timeout + 15)

    def rollout(self, namespace: str, target: str, timeout: int) -> tuple[int, str]:
        return self.run("-n", namespace, "rollout", "status", target, f"--timeout={timeout}s", timeout=timeout + 15)

    def delete_namespace(self, namespace: str) -> tuple[int, str]:
        return self.run("delete", "namespace", namespace, "--ignore-not-found", "--wait=false")

    def clues(self, namespace: str) -> str:
        """What the cluster says about a namespace: recent events and the last log lines of its pods."""
        parts = []
        _, events = self.run("-n", namespace, "get", "events", "--sort-by=.lastTimestamp")
        parts.append("EVENTS:\n" + "\n".join(events.splitlines()[-25:]))
        _, pods = self.run("-n", namespace, "get", "pods", "-o", "wide")
        parts.append("PODS:\n" + pods)
        for selector in ("role=migrate", "app"):
            _, logs = self.run("-n", namespace, "logs", "-l", selector, "--all-containers", "--tail=40", "--prefix")
            parts.append(f"LOGS ({selector}):\n{logs}")
        return "\n\n".join(parts)[-6000:]


# Common failures, recognised without a model.
KNOWN = [
    (r"ImagePullBackOff|ErrImagePull", ("The cluster cannot download the runtime image. Check DEPLOY_RUNTIME_IMAGE and "
                                        "that the cluster may pull it (public image, or an image pull secret).")),
    (r"unbound immediate PersistentVolumeClaims|no persistent volumes available|storageclass",
     "The database's disk was not created: the cluster has no default storage class. Set DEPLOY_STORAGE_CLASS."),
    (r"Insufficient (cpu|memory)", "The cluster does not have enough free CPU or memory for the app. Add a node or free space."),
    (r"exceeded quota", "The app's namespace quota is too small for what it tried to start."),
    (r"urlopen error|Name or service not known|CODE_URL", ("The pods could not download the app's code from VibeStack. "
                                                           "PUBLIC_URL must be reachable from inside the cluster.")),
    (r"connection refused.*5432|could not connect to server", "The app could not reach its database yet."),
    (r"CrashLoopBackOff|Traceback", "The app crashes when it starts; the logs below show where. Try \"Keep fixing\"."),
]


def explain(clues: str, step: str, namespace: str = "") -> str:
    """A plain-English cause: a known pattern first, otherwise the cheap model reads the cluster's clues."""
    for pattern, text in KNOWN:
        if re.search(pattern, clues, re.IGNORECASE):
            return text
    system = ("You explain Kubernetes deploy failures to a non-expert in two short sentences: the cause, then the "
              "fix. No preamble.")
    try:
        from agent.llm import ask, get_llm
        from agent.mcp_tools import load_tools
        from agent.tools import ToolSet

        llm = get_llm()
        if llm is None:
            return "The cluster did not report a clear cause; the details are below."
        # With the Kubernetes MCP server (read-only) the diagnoser looks into the cluster itself.
        tools = load_tools(purpose="deploy")
        if tools and hasattr(llm, "complete_with_tools"):
            text, _ = llm.complete_with_tools(
                "cheap", system + " Use the tools to look at the namespace's pods, events and logs first.",
                f"The step that failed: {step}\nNamespace: {namespace}\n\n{clues}", ToolSet(tools, max_calls=5))
        else:
            text, _ = ask(llm, "cheap", system, f"The step that failed: {step}\n\n{clues}", effort="low")
        return text.strip()[:600]
    except Exception as exc:  # noqa: BLE001 - the explanation is a bonus, never a second failure
        log.warning("could not explain the deploy failure: %s", exc)
    return "The cluster did not report a clear cause; the details are below."


class Deployer:
    def __init__(self, store: Store, files: Callable[[str], dict[str, str]], spec: Callable[[str], dict | None],
                 kubectl: Kubectl | None = None, http: httpx.Client | None = None) -> None:
        self.store, self.files, self.spec = store, files, spec
        self.kubectl = kubectl
        self.http = http or httpx.Client(timeout=10, follow_redirects=True)
        self._busy: set[str] = set()
        self._lock = threading.Lock()

    def _kube(self) -> Kubectl:
        if self.kubectl is None:
            self.kubectl = Kubectl()
        return self.kubectl

    # ------------------------------------------------------------------ API

    def start(self, project: dict[str, Any], run_id: str, owner_id: int, wait: bool = False) -> dict[str, Any]:
        """Deploy (or redeploy) a project's version. Runs in the background unless wait=True."""
        raw = self.spec(run_id)
        if not raw:
            raise ValueError("this version has no design to deploy")
        spec = ProjectSpec.model_validate(raw)
        current = self.store.deployment(project["id"])
        if current is None and len(self.store.deployments(owner_id)) >= settings.deploy_max_per_user:
            raise ValueError(f"you can have {settings.deploy_max_per_user} apps live at a time; undeploy one first")
        with self._lock:
            if project["id"] in self._busy:
                raise ValueError("this project is already being deployed")
            self._busy.add(project["id"])
        name, namespace, host = names(project["id"], spec)
        row = {"project_id": project["id"], "owner_id": owner_id, "run_id": run_id, "namespace": namespace,
               "url": f"https://{host}" if settings.deploy_tls_issuer else f"http://{host}",
               "status": "deploying", "message": None, "steps": json.dumps(self._fresh_steps()),
               "token": current["token"] if current else secrets.token_urlsafe(24)}
        self.store.save_deployment(row)
        job = threading.Thread(target=self._run, args=(row, spec, name), daemon=True, name=f"deploy-{project['id']}")
        job.start()
        if wait:
            job.join()
        return self.store.deployment(project["id"]) or row

    def undeploy(self, project_id: str) -> None:
        row = self.store.deployment(project_id)
        if row is None:
            return
        code, out = self._kube().delete_namespace(row["namespace"])
        if code != 0:
            raise ValueError(f"the cluster refused: {out[-300:]}")
        self.store.delete_deployment(project_id)

    def code_zip(self, project_id: str, token: str) -> bytes | None:
        """The deployed version's code, for the pods' init container (only with the deployment's own token)."""
        row = self.store.deployment(project_id)
        if row is None or not hmac.compare_digest(row["token"], token):
            return None
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, content in sorted(self.files(row["run_id"]).items()):
                if not path.startswith("tests/"):
                    zf.writestr(path, content)
        return buf.getvalue()

    def sweep(self) -> None:
        """After a restart: deploys that were in progress are marked interrupted. With DEPLOY_TTL_HOURS, old apps go."""
        for row in self.store.deployments():
            if row["status"] == "deploying" and row["project_id"] not in self._busy:
                self.store.save_deployment({"project_id": row["project_id"], "status": "failed",
                                            "message": "The deploy was interrupted by a server restart. Deploy again."})
            elif settings.deploy_ttl_hours and time.time() - row["created_at"] > settings.deploy_ttl_hours * 3600:
                log.info("removing expired deployment %s", row["namespace"])
                try:
                    self.undeploy(row["project_id"])
                except ValueError as exc:
                    log.warning("could not remove %s: %s", row["namespace"], exc)

    # ------------------------------------------------------------------ the state machine

    def _fresh_steps(self) -> list[dict[str, Any]]:
        return [{"id": sid, "label": label, "state": "waiting", "detail": ""} for sid, label in STEPS]

    def _mark(self, row: dict[str, Any], step: str, state: str, detail: str = "") -> None:
        steps = json.loads(row["steps"])
        for s in steps:
            if s["id"] == step:
                s.update(state=state, detail=detail[-1500:], at=time.time())
        row["steps"] = json.dumps(steps)
        self.store.save_deployment({"project_id": row["project_id"], "steps": row["steps"]})

    def _run(self, row: dict[str, Any], spec: ProjectSpec, name: str) -> None:
        try:
            self._deploy(row, spec, name)
            self.store.save_deployment({"project_id": row["project_id"], "status": "live", "message": None})
        except DeployError as exc:
            self._mark(row, exc.step, "failed", str(exc))
            self.store.save_deployment({"project_id": row["project_id"], "status": "failed",
                                        "message": exc.diagnosis or str(exc)})
        except Exception as exc:
            log.exception("deploy of %s crashed", row["namespace"])
            self.store.save_deployment({"project_id": row["project_id"], "status": "failed",
                                        "message": f"{type(exc).__name__}: {exc}"})
        finally:
            with self._lock:
                self._busy.discard(row["project_id"])

    def _fail(self, step: str, message: str, namespace: str) -> DeployError:
        clues = self._kube().clues(namespace)
        return DeployError(step, f"{message}\n\n{clues}", explain(f"{message}\n{clues}", step, namespace))

    def _deploy(self, row: dict[str, Any], spec: ProjectSpec, name: str) -> None:
        ns, kube = row["namespace"], self._kube()
        version = row["run_id"][:8].lower()
        db_password = derived_secret(ns, "postgres")
        self._mark(row, "render", "running")
        k8s = plugins.available()["kubernetes"]
        objects = k8s.k8s_manifests(
            spec, name=name, namespace=ns, image=settings.deploy_runtime_image, version=version,
            code_url=f"{settings.public_url}/api/deployments/{row['project_id']}/code?token={row['token']}",
            host=row["url"].split("://", 1)[1], ingress_class=settings.deploy_ingress_class,
            tls_issuer=settings.deploy_tls_issuer, storage_class=settings.deploy_storage_class,
            secrets={"SECRET_KEY": derived_secret(ns, "secret-key"), "POSTGRES_PASSWORD": db_password,
                     "DATABASE_URL": f"postgresql+psycopg://app:{db_password}@{name}-db:5432/app",
                     "ADMIN_EMAILS": self._owner_email(row["owner_id"]), "CORS_ORIGINS": row["url"]},
        )
        manifest = k8s.to_yaml(objects)
        self._mark(row, "render", "done", f"{len(objects)} objects in namespace {ns}")

        self._mark(row, "apply", "running")
        code, out = kube.apply(manifest)
        if code != 0:
            raise DeployError("apply", out, explain(out, "apply"))
        self._mark(row, "apply", "done", out)

        self._mark(row, "migrate", "running")
        code, out = kube.rollout(ns, f"statefulset/{name}-db", ROLLOUT_SECONDS)
        if code == 0:
            code, out = kube.wait_job(ns, f"{name}-migrate-{version}", ROLLOUT_SECONDS)
        if code != 0:
            raise self._fail("migrate", f"the database or its migrations did not finish: {out}", ns)
        self._mark(row, "migrate", "done", out)

        self._mark(row, "rollout", "running")
        code, out = kube.rollout(ns, f"deployment/{name}", ROLLOUT_SECONDS)
        if code != 0:
            raise self._fail("rollout", f"the app did not become ready: {out}", ns)
        self._mark(row, "rollout", "done", out)

        self._mark(row, "smoke", "running")
        detail = self._smoke(row["url"], spec)
        self._mark(row, "smoke", "done", detail)

    def _smoke(self, url: str, spec: ProjectSpec) -> str:
        """The live app answers through its public URL: health, and with logins a sign-up, login and a list."""
        deadline, last = time.time() + SMOKE_SECONDS, ""
        while True:
            try:
                r = self.http.get(f"{url}/health")
                if r.status_code == 200:
                    break
                last = f"/health answered {r.status_code}"
            except httpx.HTTPError as exc:
                last = f"{type(exc).__name__}: {exc}"
            if time.time() > deadline:
                raise DeployError("smoke", f"{url} did not answer: {last}",
                                  "The app runs, but its public address does not answer yet. Check that DNS for "
                                  f"*.{settings.deploy_domain} points at the cluster's ingress.")
            time.sleep(3)
        checks = ["GET /health → 200"]
        headers = {}
        if spec.auth:
            creds = {"email": f"smoke-{uuid.uuid4().hex[:8]}@example.com", "password": secrets.token_urlsafe(12)}
            r = self.http.post(f"{url}/auth/register", json=creds)
            if r.status_code != 201:
                raise DeployError("smoke", f"sign-up answered {r.status_code}: {r.text[:300]}")
            r = self.http.post(f"{url}/auth/login", json=creds)
            if r.status_code != 200:
                raise DeployError("smoke", f"login answered {r.status_code}: {r.text[:300]}")
            headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
            checks.append("sign-up and login work")
        table = spec.entities[0].table
        r = self.http.get(f"{url}/{table}/", headers=headers)
        if r.status_code != 200:
            raise DeployError("smoke", f"GET /{table}/ answered {r.status_code}: {r.text[:300]}")
        checks.append(f"GET /{table}/ → 200 (the database works)")
        return "; ".join(checks)

    def _owner_email(self, owner_id: int) -> str:
        return (self.store.user_by_id(owner_id) or {}).get("email", "")
