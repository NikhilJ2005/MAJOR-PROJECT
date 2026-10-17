"""Running VibeStack in production: logs, request ids, metrics, security headers and rate limits.

Standard library only, so the same image works on any host:
- logs are one JSON object per line, with the request id of the HTTP call that caused them
- /api/metrics is the Prometheus text format, read from the platform database
- security headers on every response, a body size limit, and a per-IP rate limit for logins
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
MAX_BODY_BYTES = 1_000_000  # the largest request (a 6000-character prompt) is far below this


# --------------------------------------------------------------------------- logs


class JsonFormatter(logging.Formatter):
    """One JSON object per log line: easy to search in any host's log viewer."""

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id.get(),
        }
        if record.exc_info:
            line["error"] = self.formatException(record.exc_info)
        return json.dumps(line)


def setup_logging() -> None:
    """JSON logs unless LOG_FORMAT=text (nicer when developing locally)."""
    handler = logging.StreamHandler()
    if os.environ.get("LOG_FORMAT", "json") == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)


def setup_sentry() -> None:
    """Send crashes to Sentry when SENTRY_DSN is set (the package is only needed then)."""
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger("vibestack").warning("SENTRY_DSN is set but sentry-sdk is not installed")
        return
    sentry_sdk.init(dsn=dsn, traces_sample_rate=0.0, send_default_pii=False)


# --------------------------------------------------------------------------- middleware


class RequestContext(BaseHTTPMiddleware):
    """Request id, body size limit and security headers for every HTTP request."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id.set(rid)
        try:
            if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
                return JSONResponse({"detail": "request too large"}, status_code=413)
            response = await call_next(request)
        finally:
            request_id.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        # The platform UI may not be framed by other sites; generated apps under /preview/ are
        # framed by the workspace on the same site.
        if not request.url.path.startswith("/preview/"):
            response.headers.setdefault("X-Frame-Options", "DENY")
        return response


class RateLimiter:
    """At most `limit` calls per `seconds` for each key (e.g. a client IP), in memory."""

    def __init__(self, limit: int, seconds: float) -> None:
        self.limit, self.seconds = limit, seconds
        self.calls: dict[str, deque[float]] = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self.lock:
            window = self.calls[key]
            while window and now - window[0] > self.seconds:
                window.popleft()
            if len(window) >= self.limit:
                return False
            window.append(now)
            return True


def client_ip(request: Request) -> str:
    """The caller's address (the first X-Forwarded-For hop behind a proxy such as Railway's)."""
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")


# --------------------------------------------------------------------------- metrics


def metrics_text(rows: list[dict[str, Any]], active_runs: int, previews: int) -> str:
    """Prometheus text format from the runs table (one row per run)."""
    by_status: dict[str, int] = defaultdict(int)
    for r in rows:
        by_status[r["status"]] += 1
    done = [r for r in rows if r.get("finished_at")]
    lines = ["# HELP vibestack_runs_total Builds by final status", "# TYPE vibestack_runs_total counter"]
    lines += [f'vibestack_runs_total{{status="{status}"}} {n}' for status, n in sorted(by_status.items())]

    def total(name: str, help_text: str, value: float, kind: str = "counter") -> None:
        lines.extend([f"# HELP {name} {help_text}", f"# TYPE {name} {kind}", f"{name} {value:g}"])

    total("vibestack_build_seconds_total", "Time spent building", sum(r["finished_at"] - r["created_at"] for r in done))
    total("vibestack_builds_finished_total", "Finished builds", len(done))
    total("vibestack_repairs_total", "Self-healing repairs", sum(r.get("repairs") or 0 for r in rows))
    total("vibestack_bugs_found_total", "Bugs the agents found", sum(r.get("bugs_found") or 0 for r in rows))
    total("vibestack_bugs_fixed_total", "Bugs the agents fixed", sum(r.get("bugs_fixed") or 0 for r in rows))
    total("vibestack_llm_tokens_total", "Model tokens used", sum(r.get("tokens") or 0 for r in rows))
    total("vibestack_llm_cost_usd_total", "Estimated model cost in USD", sum(r.get("cost_usd") or 0 for r in rows))
    total("vibestack_active_runs", "Builds running now", active_runs, "gauge")
    total("vibestack_live_previews", "Generated apps running as previews", previews, "gauge")
    return "\n".join(lines) + "\n"
