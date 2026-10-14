"""Finding bugs without planting them.

Two independent hunters look at the generated code:

1. A static checker built on Python's `ast` module (no AI, no tokens). It runs at every
   validation. High-severity findings fail validation, so the normal self-healing loop
   fixes them; low-severity findings become review notes in the ledger.
2. The Bug Hunter agent (an LLM) reads the requirements and the code once the contract
   tests pass, and writes adversarial tests (tests/test_edge_cases.py). Any of them that
   fails is a real bug, which the self-healing loop then repairs.

`update_bugs` keeps the list of every bug found during a run and whether it was fixed.
"""

from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass
from typing import Any

from . import prompts
from .analysis import requirements_text
from .llm import Usage, parse_file_blocks
from .state import ProjectSpec
from .testwriter import EDGE_TESTS, valid_test_file

# --------------------------------------------------------------------------- static checks


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    severity: str  # "high" blocks delivery, "low" is a review note
    message: str

    def log_line(self) -> str:
        return f"{self.path}:{self.line}: StaticCheckError [{self.rule}] {self.message}"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_SECRET_NAME = re.compile(r"secret|password|passwd|api_?key|token", re.IGNORECASE)


def _name(node: ast.AST) -> str:
    """'os.system' for os.system(...), 'eval' for eval(...)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    return ""


def check_dangerous_calls(path: str, tree: ast.AST) -> list[Finding]:
    """eval/exec and shell commands let attackers run code on the server."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _name(node.func)
            if name in ("eval", "exec") or name in ("os.system", "os.popen") or name.startswith("subprocess."):
                out.append(Finding(path, node.lineno, "dangerous-call", "high", f"{name}() can run arbitrary code"))
    return out


def check_hardcoded_secrets(path: str, tree: ast.AST) -> list[Finding]:
    """Secrets belong in environment variables, not in the source code."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            for target in node.targets:
                if isinstance(target, ast.Name) and _SECRET_NAME.search(target.id) and len(node.value.value) >= 8:
                    out.append(Finding(path, node.lineno, "hardcoded-secret", "high", f"{target.id} is hardcoded"))
        if isinstance(node, ast.Call) and _name(node.func) in ("os.getenv", "os.environ.get") and len(node.args) == 2:
            key, default = node.args
            if (isinstance(key, ast.Constant) and _SECRET_NAME.search(str(key.value))
                    and isinstance(default, ast.Constant) and isinstance(default.value, str) and len(default.value) >= 8):
                out.append(Finding(path, node.lineno, "hardcoded-secret", "high", f"{key.value} has a hardcoded fallback"))
    return out


def check_sql_strings(path: str, tree: ast.AST) -> list[Finding]:
    """SQL built from f-strings or + / % invites SQL injection; use bound parameters."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args:
            name = _name(node.func).rsplit(".", 1)[-1]
            arg = node.args[0]
            built = isinstance(arg, ast.JoinedStr) or (isinstance(arg, ast.BinOp) and isinstance(arg.op, (ast.Add, ast.Mod)))
            if name in ("execute", "raw", "text", "extra") and built:
                out.append(Finding(path, node.lineno, "sql-injection", "high", f"{name}() gets SQL built from strings"))
    return out


def check_debug_mode(path: str, tree: ast.AST) -> list[Finding]:
    """Debug mode leaks stack traces and settings to every visitor."""
    out = []
    for node in ast.walk(tree):
        is_true = isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value is True
        if is_true and any(isinstance(t, ast.Name) and t.id == "DEBUG" for t in node.targets):
            out.append(Finding(path, node.lineno, "debug-on", "high", "DEBUG is hardcoded to True"))
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "debug" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    out.append(Finding(path, node.lineno, "debug-on", "high", "debug=True is hardcoded"))
    return out


def check_bare_except(path: str, tree: ast.AST) -> list[Finding]:
    """`except:` also swallows KeyboardInterrupt and hides real errors."""
    return [Finding(path, n.lineno, "bare-except", "low", "bare `except:`; catch a specific exception")
            for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler) and n.type is None]


def check_print(path: str, tree: ast.AST) -> list[Finding]:
    """print() output is lost in production; use logging."""
    return [Finding(path, n.lineno, "print", "low", "print() left in code; use logging")
            for n in ast.walk(tree) if isinstance(n, ast.Call) and _name(n.func) == "print"]


CHECKS = [check_dangerous_calls, check_hardcoded_secrets, check_sql_strings, check_debug_mode, check_bare_except, check_print]


def static_check(files: dict[str, str]) -> list[Finding]:
    """Run every check on the application code (tests and migrations are skipped)."""
    findings: list[Finding] = []
    for path, source in sorted(files.items()):
        if not path.endswith(".py") or not path.startswith("app/") or "/migrations/" in path:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue  # the import gate reports syntax errors with a proper traceback
        for check in CHECKS:
            findings += check(path, tree)
    return findings


# --------------------------------------------------------------------------- the Bug Hunter agent


def code_for_review(spec: ProjectSpec, files: dict[str, str], limit: int = 40_000) -> str:
    """Schemas, services and routers of every entity, cut to a size a model can read."""
    parts = []
    for e in spec.entities:
        for kind in ("schemas", "services", "routers"):
            path = f"app/{kind}/{e.module}.py"
            if path in files:
                parts.append(f"### {path}\n```python\n{files[path]}```")
    text = "\n\n".join(parts)
    return text if len(text) <= limit else text[:limit] + "\n...[truncated]"


def write_edge_tests(llm: Any, spec: ProjectSpec, files: dict[str, str]) -> tuple[str | None, str, Usage]:
    """Ask the Bug Hunter for tests/test_edge_cases.py. Returns (source or None, focus, usage)."""
    system = prompts.BUG_HUNTER_SYSTEM.format(helpers=prompts.TEST_HELPERS, contract=prompts.CONTRACT)
    user = prompts.BUG_HUNTER_USER.format(
        requirements=requirements_text(spec.requirements),
        spec=spec.model_dump_json(indent=1, exclude={"requirements"}),
        code=code_for_review(spec, files),
    )
    text, usage = llm.complete("cheap", system, user)
    source = parse_file_blocks(text).get(EDGE_TESTS)
    m = re.search(r"FOCUS:\s*(.+)", text)
    focus = m.group(1).strip() if m else "edge cases"
    return (source if source and valid_test_file(source) else None), focus, usage


# --------------------------------------------------------------------------- bug tracking

_FAILED = re.compile(r"^(?:FAILED|ERROR) (tests/[\w/]+\.py)::(\S+)", re.MULTILINE)
_FINDERS = {
    "tests/test_smoke.py": "contract tests",
    "tests/test_rules.py": "test engineer",
    "tests/test_edge_cases.py": "bug hunter",
}


def failing_tests(log: str) -> list[tuple[str, str]]:
    """(file, test name) for every failed test in a pytest summary."""
    return _FAILED.findall(log)


def problems_in(validation: dict[str, Any]) -> list[dict[str, str]]:
    """What is wrong according to one validation result, and who found it."""
    log = validation.get("log", "")
    gate = validation.get("gate", "")
    if gate == "static":
        return [{"id": line.split(" StaticCheckError ")[-1][:120], "found_by": "static checker", "detail": line}
                for line in log.splitlines() if "StaticCheckError" in line]
    if gate == "tests" and failing_tests(log):
        return [{"id": f"{path}::{name}", "found_by": _FINDERS.get(path, "tests"), "detail": name}
                for path, name in failing_tests(log)]
    errors = [ln.strip() for ln in log.splitlines() if re.search(r"\w+Error\b", ln)]
    first = errors[-1] if errors else f"{gate} gate failed"
    return [{"id": f"{gate}: {re.sub(r'[0-9]+', 'N', first)[:120]}", "found_by": "validator", "detail": first[:200]}]


def update_bugs(bugs: list[dict[str, Any]], validation: dict[str, Any]) -> list[dict[str, Any]]:
    """Add newly found problems; when validation passes, everything found so far counts as fixed."""
    bugs = [dict(b) for b in bugs]
    if validation.get("ok"):
        for b in bugs:
            b["fixed"] = not b.get("open", False)  # "open": left unfixed on purpose (see nodes.hunt_giveup)
        return bugs
    known = {b["id"] for b in bugs}
    for problem in problems_in(validation):
        if problem["id"] not in known:
            bugs.append({**problem, "fixed": False})
            known.add(problem["id"])
    return bugs
