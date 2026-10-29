"""Tools the Reflector can call while it repairs a project.

Instead of guessing a fix in one shot, the Reflector works like a developer: it reads the files
it needs, searches the code, writes a fix, runs the failing tests and only then answers. Each
tool is a plain Python function with a docstring; `Tool` turns it into the JSON description
that function-calling models understand.

    list_files()                  every file in the project
    read_file(path)               one file
    search_code(pattern)          lines matching a regular expression
    list_routes()                 the HTTP routes the design promises
    write_file(path, content)     stage a fix (application files only, never tests)
    run_tests(selection)          run some tests against the staged fix
    check_frontend()              the frontend contract check

Plugins can add their own tools (plugins.py, `tools()` hook) and so can external MCP servers
(mcp_tools.py). Every call is recorded, and the ledger shows them.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .frontend import check_frontend
from .healing import editable
from .ledger import progress
from .state import ProjectSpec

MAX_TOOL_CALLS = 6
MAX_RESULT_CHARS = 4000
_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}
_SELECTION = re.compile(r"^tests/[\w/.\-]+\.py(::[\w\[\]\-.]+)?$")


@dataclass
class Tool:
    """A function the model may call: its name, what it does, its parameters (JSON schema)."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]

    def schema(self) -> dict[str, Any]:
        """The OpenAI function-calling format, understood by every provider we support."""
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


def tool_from_function(fn: Callable[..., Any], name: str | None = None) -> Tool:
    """Describe a plain function as a tool: parameters from its signature, description from its docstring."""
    props: dict[str, Any] = {}
    required: list[str] = []
    for p in inspect.signature(fn).parameters.values():
        hint = p.annotation if p.annotation in _JSON_TYPES else str
        props[p.name] = {"type": _JSON_TYPES[hint]}
        if p.default is inspect.Parameter.empty:
            required.append(p.name)
    doc = inspect.getdoc(fn) or (name or fn.__name__)
    return Tool(name or fn.__name__, doc.split("\n\n")[0], {"type": "object", "properties": props, "required": required}, fn)


_SAY = {
    "read_file": "Reading {}", "search_code": "Searching the code for “{}”", "list_files": "Looking through the files",
    "list_routes": "Checking which endpoints exist", "write_file": "Staging a fix to {}",
    "run_tests": "Re-running the failing tests", "check_frontend": "Checking the screens against the API",
}


def _say(tool: str, arg: str) -> str:
    """What the Reflector is doing, in words (shown live in the UI)."""
    text = _SAY.get(tool)
    if text is None:
        return f"{tool.replace('_', ' ').capitalize()} {arg}".strip()
    return text.format(arg) if "{}" in text else text


def routes(spec: ProjectSpec) -> list[str]:
    """Every HTTP route of the generated backend, as 'METHOD /path'."""
    out = ["GET /health"]
    if spec.auth:
        out += ["POST /auth/register", "POST /auth/login", "GET /auth/me"]
    for e in spec.entities:
        t = e.table
        out += [f"GET /{t}/", f"POST /{t}/", f"GET /{t}/{{item_id}}", f"PATCH /{t}/{{item_id}}", f"DELETE /{t}/{{item_id}}"]
        if e.realtime:
            out.append(f"GET /{t}/stream")
    by_name = {e.name: e for e in spec.entities}
    for a in spec.actions:
        if a.entity in by_name:
            out.append(f"{a.method} /{by_name[a.entity].table}{a.path}")
    return out


class Toolbox:
    """The Reflector's workbench: a copy of the project, the tools, and a record of every call."""

    def __init__(self, files: dict[str, str], spec: ProjectSpec, extra: list[Tool] | None = None,
                 max_calls: int = MAX_TOOL_CALLS) -> None:
        self.original = files
        self.files = dict(files)
        self.spec = spec
        self.max_calls = max_calls
        self.calls: list[dict[str, Any]] = []
        own = [self.list_files, self.read_file, self.search_code, self.list_routes,
               self.write_file, self.run_tests, self.check_frontend]
        self.tools = [tool_from_function(f) for f in own] + list(extra or [])

    # -- the tools ------------------------------------------------------------------------

    def list_files(self) -> str:
        """List every file in the project."""
        return "\n".join(sorted(self.files))

    def read_file(self, path: str) -> str:
        """Read one file of the project, e.g. app/routers/order.py."""
        if path not in self.files:
            return f"no such file: {path}"
        return self.files[path]

    def search_code(self, pattern: str) -> str:
        """Find lines matching a regular expression in all Python files, as path:line: text."""
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return f"invalid pattern: {exc}"
        hits = [
            f"{path}:{n}: {line.strip()}"
            for path, body in sorted(self.files.items()) if path.endswith(".py")
            for n, line in enumerate(body.splitlines(), 1) if rx.search(line)
        ]
        return "\n".join(hits[:60]) or "no matches"

    def list_routes(self) -> str:
        """List the HTTP routes the design promises (method and path)."""
        return "\n".join(routes(self.spec))

    def write_file(self, path: str, content: str) -> str:
        """Replace a business-logic file (app/services/*.py) or app/static/ui.json with a fixed version."""
        if path.startswith("tests/"):
            return "refused: tests are the specification; fix the code instead"
        if path not in self.files:
            return f"refused: {path} is not a file of this project"
        if not editable(path):
            return f"refused: {path} is verified template code; fix the business logic in app/services/ instead"
        self.files[path] = content if content.endswith("\n") else content + "\n"
        return f"staged {path} ({len(content.splitlines())} lines); call run_tests to check it"

    def run_tests(self, selection: str = "") -> str:
        """Run tests against the staged files. selection: e.g. 'tests/test_smoke.py::test_crud' (empty = all)."""
        from .sandbox import get_sandbox

        chosen = selection.split()
        if any(not _SELECTION.match(s) for s in chosen):
            return "selection must look like tests/<file>.py or tests/<file>.py::<test_name>"
        ok, log = get_sandbox().run_tests(self.files, self.spec.stack, chosen)
        counts = re.findall(r"\d+ (?:passed|failed|errors?)", log)
        return f"{'PASSED' if ok else 'FAILED'} ({', '.join(counts) or 'no tests ran'})\n{log[-3000:]}"

    def check_frontend(self) -> str:
        """Check that the frontend's config files match the backend design."""
        problems = check_frontend(self.files, self.spec)
        return "\n".join(problems) or "frontend matches the backend"

    # -- calling ----------------------------------------------------------------------------

    def call(self, name: str, args: dict[str, Any]) -> str:
        """Run one tool call from the model; never raises, always returns text for the model."""
        if len(self.calls) >= self.max_calls:
            return "tool budget used up: give your final answer now"
        tool = next((t for t in self.tools if t.name == name), None)
        first = next(iter((args or {}).values()), "")
        progress("reflect", _say(name, first if isinstance(first, str) and len(first) < 80 else ""))
        try:
            result = "unknown tool" if tool is None else str(tool.fn(**(args or {})))
        except Exception as exc:  # noqa: BLE001 - a bad call is reported to the model, not raised
            result = f"error: {type(exc).__name__}: {exc}"
        shown = {k: (v if len(str(v)) < 80 else f"<{len(str(v))} chars>") for k, v in (args or {}).items()}
        self.calls.append({"tool": name, "args": shown, "result": result.splitlines()[0][:120] if result else ""})
        return result[:MAX_RESULT_CHARS]

    def changes(self) -> dict[str, str]:
        """Files the model rewrote with write_file."""
        return {p: body for p, body in self.files.items() if body != self.original.get(p)}


class ToolSet:
    """Tools for an agent outside the repair loop (e.g. the deploy diagnoser): same budget and record as Toolbox."""

    def __init__(self, tools: list[Tool], max_calls: int = MAX_TOOL_CALLS, node: str = "deploy") -> None:
        self.tools, self.max_calls, self.node = tools, max_calls, node
        self.calls: list[dict[str, Any]] = []

    def call(self, name: str, args: dict[str, Any]) -> str:
        if len(self.calls) >= self.max_calls:
            return "tool budget used up: give your final answer now"
        tool = next((t for t in self.tools if t.name == name), None)
        try:
            result = "unknown tool" if tool is None else str(tool.fn(**(args or {})))
        except Exception as exc:  # noqa: BLE001 - a bad call is reported to the model, not raised
            result = f"error: {type(exc).__name__}: {exc}"
        self.calls.append({"tool": name, "args": args or {}, "result": result.splitlines()[0][:120] if result else ""})
        return result[:MAX_RESULT_CHARS]
