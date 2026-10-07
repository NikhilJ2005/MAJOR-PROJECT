"""Live previews: run a generated backend as a long-lived process and proxy to it.

This is the Lovable-style "see it running" step. The generated project is
started with the same isolation as the validator (scrubbed environment, own
process group, unprivileged `sandbox` user) on a private localhost port, and
reached through the main server at /preview/<run_id>/..., so it works on a
single-port host such as Railway.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO

import httpx

from agent.sandbox import LocalSandbox, free_port, write_tree

log = logging.getLogger(__name__)

MAX_PREVIEWS = 2
IDLE_TIMEOUT_S = 20 * 60
BOOT_TIMEOUT_S = 25


@dataclass
class Preview:
    run_id: str
    port: int
    proc: subprocess.Popen
    workdir: Path
    logfile: IO[str]
    started_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class PreviewError(RuntimeError):
    pass


class PreviewManager:
    def __init__(self) -> None:
        self._previews: dict[str, Preview] = {}
        self._lock = threading.Lock()
        self._sandbox = LocalSandbox()

    def get(self, run_id: str) -> Preview | None:
        p = self._previews.get(run_id)
        if p and p.alive:
            p.last_used = time.time()
            return p
        return None

    def start(self, run_id: str, files: dict[str, str]) -> Preview:
        """Start (or restart) the preview for a run. Blocks until /health answers."""
        with self._lock:
            self._reap_idle()
            self._stop(run_id)
            while len(self._previews) >= MAX_PREVIEWS:
                oldest = min(self._previews.values(), key=lambda p: p.last_used)
                self._stop(oldest.run_id)

            # A private temp workspace (like the validator's): the sandbox user must be
            # able to reach it, which is not guaranteed under the server's data dir.
            workdir = Path(tempfile.mkdtemp(prefix=f"vibestack-preview-{run_id}-"))
            write_tree(workdir, files)
            self._sandbox.hand_over(workdir)
            port = free_port()
            logfile = open(workdir / "preview.log", "w")  # noqa: SIM115 - owned by the process
            proc = self._sandbox.spawn(
                [
                    sys.executable, "-m", "uvicorn", "app.main:app",
                    "--host", "127.0.0.1", "--port", str(port),
                    "--root-path", f"/preview/{run_id}",
                ],
                workdir,
                stdout=logfile,
            )
            preview = Preview(run_id, port, proc, workdir, logfile)
            self._previews[run_id] = preview

        deadline = time.monotonic() + BOOT_TIMEOUT_S
        while time.monotonic() < deadline:
            if not preview.alive:
                break
            try:
                if httpx.get(f"{preview.base_url}/health", timeout=1.0, trust_env=False).status_code == 200:
                    return preview
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        output = (preview.workdir / "preview.log").read_text()[-2000:]
        self.stop(run_id)
        raise PreviewError(f"generated app did not start:\n{output}")

    def stop(self, run_id: str) -> None:
        with self._lock:
            self._stop(run_id)

    def stop_all(self) -> None:
        with self._lock:
            for run_id in list(self._previews):
                self._stop(run_id)

    def _stop(self, run_id: str) -> None:
        p = self._previews.pop(run_id, None)
        if p is None:
            return
        if p.alive:
            try:
                os.killpg(p.proc.pid, signal.SIGTERM)
                p.proc.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(p.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        p.logfile.close()
        shutil.rmtree(p.workdir, ignore_errors=True)
        log.info("stopped preview %s", run_id)

    def _reap_idle(self) -> None:
        now = time.time()
        for run_id, p in list(self._previews.items()):
            if not p.alive or now - p.last_used > IDLE_TIMEOUT_S:
                self._stop(run_id)
