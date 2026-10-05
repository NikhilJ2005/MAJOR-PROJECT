"""Error classification and fault localisation for the self-healing loop.

Pure functions over the validation log, so they are cheap (no LLM tokens),
deterministic, and unit-testable.
"""

from __future__ import annotations

import re

from .state import ProjectSpec

# Order matters: the first matching rule wins.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("timeout", re.compile(r"TimeoutError")),
    ("syntax", re.compile(r"\b(SyntaxError|IndentationError|TabError)\b")),
    ("dependency", re.compile(r"ModuleNotFoundError: No module named '(?!app\b)[\w.]+'")),
    ("import", re.compile(r"\b(ImportError|ModuleNotFoundError)\b|cannot import name")),
    ("orm", re.compile(r"sqlalchemy\.exc\.|NoForeignKeysError|NoReferencedTableError|Mapper|mapped_column")),
    ("validation", re.compile(r"pydantic[\w.]*\.?(ValidationError|PydanticUserError|PydanticSchemaGenerationError)|\b422\b.*Unprocessable|assert 422")),
    ("runtime", re.compile(r"\b(NameError|AttributeError|TypeError|KeyError|ValueError)\b")),
    ("contract", re.compile(r"AssertionError|assert .* ==|\bFAILED\b")),
]


def classify_error(log: str) -> str:
    for name, pattern in _RULES:
        if pattern.search(log):
            return name
    return "unknown"


def locate_failing_file(log: str, files: dict[str, str], spec: ProjectSpec) -> str | None:
    """Best guess at the generated file that holds the root cause."""
    # 1. Python tracebacks: the deepest frame inside generated app code.
    frames = re.findall(r'File "(app/[\w/]+\.py)", line \d+', log)
    # 2. pytest's short tracebacks: "app/routers/post.py:31: in create_post"
    frames += re.findall(r"^(app/[\w/]+\.py):\d+", log, re.M)
    candidates = [f for f in frames if f in files]
    if candidates:
        # Prefer the last frame that appears in the log (deepest call).
        last_pos = {f: log.rfind(f) for f in candidates}
        return max(last_pos, key=last_pos.get)
    # 3. A failing parametrised smoke test names the entity.
    m = re.search(r"test_crud\[(\w+)\]", log)
    if m:
        for e in spec.entities:
            if e.name == m.group(1):
                return f"app/routers/{e.module}.py"
    if "test_auth_flow" in log and "app/routers/auth.py" in files:
        return "app/routers/auth.py"
    # 4. Any generated path mentioned anywhere.
    for path in re.findall(r"(app/[\w/]+\.py)", log):
        if path in files:
            return path
    return None


def related_files(path: str, files: dict[str, str]) -> list[str]:
    """Delta-only context: the failing file's siblings for the same entity, nothing else."""
    module = path.rsplit("/", 1)[-1]
    related = [
        p
        for p in (f"app/models/{module}", f"app/schemas/{module}", f"app/routers/{module}", "app/database.py")
        if p in files and p != path
    ]
    if "app/security.py" in files and path.startswith("app/routers/"):
        related.append("app/security.py")
    return related
