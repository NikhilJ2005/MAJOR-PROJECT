"""Validation sandbox: the external, objective signal for the self-healing loop.

Three gates, cheapest first; the first failing gate stops validation:
  1. import  - import the whole app (syntax errors, bad imports, mapper errors)
  2. boot    - the framework's server starts and GET /health returns 200
  3. tests   - pytest runs the spec-derived contract tests (+ rule / edge-case tests)

What "import" and "boot" mean per framework comes from stacks.py.

`LocalSandbox` runs each gate as a subprocess with a scrubbed environment (no
API keys leak to generated code), its own process group, a hard timeout and OS
resource limits (memory, CPU time, file size, open files, processes).
On Railway this is the only option (no Docker daemon); the hosting container
is the isolation boundary. A Docker backend can implement the same protocol.
"""

from __future__ import annotations

import os
import pwd
import resource
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import settings
from .stacks import Stack, get_stack

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
    def validate(self, files: dict[str, str], stack: str = "fastapi") -> GateResult: ...

    def run_tests(self, files: dict[str, str], stack: str, selection: list[str]) -> tuple[bool, str]: ...

    def make_migrations(self, files: dict[str, str], stack: str) -> tuple[dict[str, str], str]: ...


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


def free_port() -> int:
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


def resource_limits(cpu_seconds: int, memory_mb: int, limit_processes: bool):
    """Build the function the child process runs before the generated code starts.

    Limits are enforced by the operating system, so even a fork bomb or a 10 GB
    allocation in generated code fails inside the sandbox instead of hurting the server.
    """

    def apply() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (100 * 1024 * 1024,) * 2)  # 100 MB per file
        resource.setrlimit(resource.RLIMIT_NOFILE, (512, 512))
        if limit_processes:  # only as the dedicated sandbox user: the limit is per user
            resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))

    return apply


class LocalSandbox:
    def __init__(self, timeout_s: int | None = None) -> None:
        self.timeout_s = timeout_s or settings.gate_timeout_s
        self.identity = _sandbox_identity()

    def hand_over(self, workdir: Path) -> None:
        """Give the sandbox user ownership of a workspace it must write to (SQLite files)."""
        if self.identity:
            for path in [workdir, *workdir.rglob("*")]:
                os.chown(path, *self.identity)

    def spawn(
        self, args: list[str], workdir: Path, stdout: Any = subprocess.PIPE, cpu_seconds: int | None = None
    ) -> subprocess.Popen:
        """Start a process for generated code: scrubbed env, own process group, unprivileged user, limits."""
        kwargs: dict[str, Any] = {}
        if self.identity:
            kwargs = {"user": self.identity[0], "group": self.identity[1], "extra_groups": []}
        kwargs["preexec_fn"] = resource_limits(
            cpu_seconds or self.timeout_s * 2, settings.sandbox_memory_mb, limit_processes=bool(self.identity)
        )
        return subprocess.Popen(
            args,
            cwd=workdir,
            env=self._env(workdir),
            stdout=stdout,
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
            "ENV": "test",  # generated apps: create tables on start, throwaway SECRET_KEY
            "ADMIN_EMAILS": "admin@vibestack.dev",  # the live preview's demo admin account
            "DJANGO_SETTINGS_MODULE": "app.settings",
            "FLASK_SKIP_DOTENV": "1",
            # Host plugins (e.g. langsmith) must not leak into the generated project's tests.
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        }

    def _clean(self, text: str, workdir: Path) -> str:
        return _tail(text.replace(str(workdir.resolve()) + "/", "").replace(str(workdir) + "/", ""))

    def _run(self, args: list[str], workdir: Path) -> tuple[int, str]:
        proc = self.spawn(args, workdir)
        try:
            out, _ = proc.communicate(timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            out, _ = proc.communicate()
            return 124, f"{out}\nTimeoutError: gate exceeded {self.timeout_s}s"
        return proc.returncode, out

    def _gate_import(self, workdir: Path, stack: Stack) -> tuple[bool, str]:
        code, out = self._run([sys.executable, "-c", stack.import_check], workdir)
        return code == 0, out

    def prepare(self, workdir: Path, stack: Stack) -> tuple[bool, str]:
        """Framework set-up before the server can start (Django: create the tables)."""
        for args in stack.prepare:
            code, out = self._run([sys.executable, *args], workdir)
            if code != 0:
                return False, out
        return True, ""

    def _gate_boot(self, workdir: Path, stack: Stack) -> tuple[bool, str]:
        ok, out = self.prepare(workdir, stack)
        if not ok:
            return False, out
        port = free_port()
        proc = self.spawn([sys.executable, *stack.boot_args(port)], workdir)
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

    def _gate_tests(self, workdir: Path, stack: Stack) -> tuple[bool, str]:
        code, out = self._run(
            # No -x: we want the full failing count, so healing can tell whether it made progress.
            [sys.executable, "-m", "pytest", "-q", "-rfE", "--tb=short", "--no-header", "-p", "no:cacheprovider", "tests"],
            workdir,
        )
        return code == 0, out

    def validate(self, files: dict[str, str], stack: str = "fastapi") -> GateResult:
        chosen = get_stack(stack)
        workdir = Path(tempfile.mkdtemp(prefix="vibestack-"))
        t0 = time.monotonic()
        try:
            write_tree(workdir, files)
            self.hand_over(workdir)
            for gate, fn in (
                ("import", self._gate_import),
                ("boot", self._gate_boot),
                ("tests", self._gate_tests),
            ):
                ok, out = fn(workdir, chosen)
                if not ok:
                    return GateResult(False, gate, self._clean(out, workdir), time.monotonic() - t0)
            return GateResult(True, "tests", self._clean(out, workdir), time.monotonic() - t0)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


    def run_tests(self, files: dict[str, str], stack: str, selection: list[str]) -> tuple[bool, str]:
        """Run some of the project's tests (the Reflector's run_tests tool); all of them when selection is empty."""
        chosen = get_stack(stack)
        workdir = Path(tempfile.mkdtemp(prefix="vibestack-tool-"))
        try:
            write_tree(workdir, files)
            self.hand_over(workdir)
            ok, out = self.prepare(workdir, chosen)
            if ok:
                code, out = self._run(
                    [sys.executable, "-m", "pytest", "-q", "-rfE", "--tb=short", "--no-header", "-p", "no:cacheprovider",
                     *(selection or ["tests"])],
                    workdir,
                )
                ok = code == 0
            return ok, self._clean(out, workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def make_migrations(self, files: dict[str, str], stack: str) -> tuple[dict[str, str], str]:
        """Create the initial migration with the framework's own tool and apply it once.

        Returns (new files, ledger note). On failure the project ships without them
        (dev/test still create tables automatically) and the note says why.
        """
        chosen = get_stack(stack)
        workdir = Path(tempfile.mkdtemp(prefix="vibestack-migrate-"))
        try:
            write_tree(workdir, files)
            self.hand_over(workdir)
            for args in chosen.migration_steps:
                code, out = self._run([sys.executable, *args], workdir)
                if code != 0:
                    return {}, f"migrations skipped: `{' '.join(args)}` failed: {self._clean(out, workdir)[-300:]}"
            folder = workdir / chosen.migration_dir
            new = {
                str(p.relative_to(workdir)): p.read_text()
                for p in sorted(folder.rglob("*.py"))
                if "__pycache__" not in p.parts and str(p.relative_to(workdir)) not in files
            }
            return new, f"created and applied {len(new)} migration file(s) in {chosen.migration_dir}/"
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


def get_sandbox() -> Sandbox:
    if settings.sandbox == "local":
        return LocalSandbox()
    raise RuntimeError(
        f"SANDBOX={settings.sandbox!r} is not available in this build; use SANDBOX=local"
    )
