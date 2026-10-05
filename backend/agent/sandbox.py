"""Validation sandbox: the external, objective signal for the self-healing loop.

Three gates, cheapest first; the first failing gate stops validation:
  1. import  - `import app.main` (syntax errors, bad imports, mapper errors)
  2. boot    - uvicorn starts and GET /health returns 200
  3. tests   - pytest runs the spec-derived smoke tests

`LocalSandbox` runs each gate as a subprocess with a scrubbed environment (no
API keys leak to generated code), its own process group and a hard timeout.
On Railway this is the only option (no Docker daemon); the hosting container
is the isolation boundary. A Docker backend can implement the same protocol.
"""

from __future__ import annotations

import os
import pwd
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import httpx

from .config import settings

MAX_LOG_CHARS = 6000


@dataclass
class GateResult:
    ok: bool
    gate: str
    log: str
    duration_s: float

    def as_state(self) -> dict:
        return asdict(self)


class Sandbox(Protocol):
    def validate(self, files: dict[str, str]) -> GateResult: ...


def write_tree(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        target = (root / rel).resolve()
        if root.resolve() not in target.parents:
            raise ValueError(f"refusing to write outside workspace: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def _tail(text: str, limit: int = MAX_LOG_CHARS) -> str:
    """Keep the end of the log, where the root cause of a Python failure is printed."""
    return text if len(text) <= limit else "...[truncated]...\n" + text[-limit:]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _sandbox_identity() -> tuple[int, int] | None:
    """(uid, gid) of the unprivileged sandbox user, when we can switch to it.

    Generated code must not run as the server's user: it could read the server's
    environment (API key) through /proc/<pid>/environ. In the Docker image the
    server runs as root and every gate drops to the `sandbox` user.
    """
    if os.geteuid() != 0 or not settings.sandbox_user:
        return None
    try:
        pw = pwd.getpwnam(settings.sandbox_user)
    except KeyError:
        return None
    return pw.pw_uid, pw.pw_gid


class LocalSandbox:
    def __init__(self, timeout_s: int | None = None) -> None:
        self.timeout_s = timeout_s or settings.gate_timeout_s
        self.identity = _sandbox_identity()

    def _popen(self, args: list[str], workdir: Path) -> subprocess.Popen:
        kwargs = {}
        if self.identity:
            kwargs = {"user": self.identity[0], "group": self.identity[1], "extra_groups": []}
        return subprocess.Popen(
            args,
            cwd=workdir,
            env=self._env(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
            **kwargs,
        )

    def _env(self, workdir: Path) -> dict[str, str]:
        return {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(workdir),
            "PYTHONPATH": str(workdir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "DATABASE_URL": f"sqlite:///{workdir}/sandbox.db",
            "LANG": "C.UTF-8",
            # Host plugins (e.g. langsmith) must not leak into the generated project's tests.
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }

    def _clean(self, text: str, workdir: Path) -> str:
        return _tail(text.replace(str(workdir.resolve()) + "/", "").replace(str(workdir) + "/", ""))

    def _run(self, args: list[str], workdir: Path) -> tuple[int, str]:
        proc = self._popen(args, workdir)
        try:
            out, _ = proc.communicate(timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, _ = proc.communicate()
            return 124, f"{out}\nTimeoutError: gate exceeded {self.timeout_s}s"
        return proc.returncode, out

    def _gate_import(self, workdir: Path) -> tuple[bool, str]:
        code, out = self._run([sys.executable, "-c", "import app.main"], workdir)
        return code == 0, out

    def _gate_boot(self, workdir: Path) -> tuple[bool, str]:
        port = _free_port()
        proc = self._popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            workdir,
        )
        deadline = time.monotonic() + min(self.timeout_s, 30)
        ok, detail = False, ""
        try:
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    detail = "server process exited during startup"
                    break
                try:
                    r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=1.0, trust_env=False)
                    ok = r.status_code == 200
                    detail = f"GET /health -> {r.status_code} {r.text[:200]}"
                    break
                except httpx.HTTPError:
                    time.sleep(0.25)
            else:
                detail = "TimeoutError: /health did not respond"
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
            out, _ = proc.communicate()
        return ok, f"{out}\n{detail}"

    def _gate_tests(self, workdir: Path) -> tuple[bool, str]:
        code, out = self._run(
            [sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider", "tests"],
            workdir,
        )
        return code == 0, out

    def validate(self, files: dict[str, str]) -> GateResult:
        workdir = Path(tempfile.mkdtemp(prefix="vibestack-"))
        t0 = time.monotonic()
        try:
            write_tree(workdir, files)
            if self.identity:
                for path in [workdir, *workdir.rglob("*")]:
                    os.chown(path, *self.identity)
            for gate, fn in (
                ("import", self._gate_import),
                ("boot", self._gate_boot),
                ("tests", self._gate_tests),
            ):
                ok, out = fn(workdir)
                if not ok:
                    return GateResult(False, gate, self._clean(out, workdir), time.monotonic() - t0)
            return GateResult(True, "tests", self._clean(out, workdir), time.monotonic() - t0)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


def get_sandbox() -> Sandbox:
    if settings.sandbox == "local":
        return LocalSandbox()
    raise RuntimeError(
        f"SANDBOX={settings.sandbox!r} is not available in this build; use SANDBOX=local"
    )
