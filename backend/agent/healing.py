"""Error classification and fault localisation for the self-healing loop.

Pure functions over the validation log, so they are cheap (no LLM tokens),
deterministic, and unit-testable.
"""

from __future__ import annotations

import hashlib
import re

from .state import ProjectSpec

# Order matters: the first matching rule wins.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("static", re.compile(r"StaticCheckError")),
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
    frames += re.findall(r"^(app/[\w/]+\.py):\d+", log, re.MULTILINE)
    candidates = [f for f in frames if f in files]
    if candidates:
        # Prefer the last frame that appears in the log (deepest call).
        last_pos = {f: log.rfind(f) for f in candidates}
        return max(last_pos, key=last_pos.get)
    # 2b. A failing rule/edge-case test: find the entity (and action) it calls.
    for test_path, name in re.findall(r"^(?:FAILED|ERROR) (tests/[\w/]+\.py)::(\w+)", log, re.MULTILINE):
        if test_path in files and test_path != "tests/test_smoke.py":
            target = file_for_test(_function_source(files[test_path], name), spec)
            if target and target in files:
                return target
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


def _function_source(source: str, name: str) -> str:
    """The text of `def name(...)` up to the next top-level definition (good enough for matching URLs)."""
    m = re.search(rf"^def {re.escape(name)}\(.*?(?=^\S|\Z)", source, re.MULTILINE | re.DOTALL)
    return m.group(0) if m else ""


def file_for_test(test_source: str, spec: ProjectSpec) -> str | None:
    """Which generated file a failing test is about: the service for an action, else the router."""
    for e in spec.entities:
        if f"/{e.table}" not in test_source:
            continue
        for a in spec.actions_for(e):
            if f"/{a.segment}" in test_source:
                return f"app/services/{e.module}.py"
    for e in spec.entities:
        if f"/{e.table}" in test_source:
            return f"app/routers/{e.module}.py"
    return None


def related_files(path: str, files: dict[str, str]) -> list[str]:
    """Delta-only context: the failing file's siblings for the same entity, nothing else."""
    module = path.rsplit("/", 1)[-1]
    related = [
        p
        for p in (
            f"app/models/{module}", f"app/schemas/{module}", f"app/services/{module}", f"app/routers/{module}",
            "app/database.py", "app/errors.py",
        )
        if p in files and p != path
    ]
    if "app/security.py" in files and path.startswith("app/routers/"):
        related.append("app/security.py")
    return related


# --------------------------------------------------------------------------- when to stop healing
#
# Instead of a fixed "3 attempts", healing continues while it makes progress.
# After every validation we store a small record (see `history_entry`); the
# rules below read that history:
#
#   1. progress (a later gate reached, fewer failing tests, or a never-seen error
#      because the previous one got fixed) -> keep going,
#      and drop back to the cheap model for the next (new) problem
#   2. no progress -> climb one rung of the ladder: cheap -> strong -> templates
#   3. top rung tried without progress                 -> stop
#   4. code identical to an earlier version (circles)  -> stop
#   5. token budget spent                               -> stop
#   6. MAX_HEAL_SAFETY attempts (a seatbelt, not the rule) -> stop

GATE_ORDER = ["static", "import", "boot", "tests"]  # the order validation runs them
LADDER_WITH_LLM = ["cheap", "strong", "template"]
LADDER_OFFLINE = ["template"]


def failing_count(log: str) -> int | None:
    """Number of failed tests in a pytest summary ("3 failed, 1 error"), None if no summary."""
    counts = [int(n) for n in re.findall(r"(\d+) (?:failed|errors?)\b", log)]
    if counts:
        return sum(counts)
    return 0 if re.search(r"\d+ passed", log) else None


def error_signature(gate: str, log: str) -> str:
    """A short, stable description of *what* failed (numbers removed so line shifts don't matter)."""
    lines = [ln.strip() for ln in log.splitlines() if re.search(r"Error|FAILED|assert", ln)]
    first = lines[-1] if lines else log.strip()[-200:]
    return f"{gate}: {re.sub(r'[0-9]+', 'N', first)[:200]}"


def fingerprint(files: dict[str, str]) -> str:
    """sha256 over every file, so we notice when healing brings back an earlier version."""
    h = hashlib.sha256()
    for path in sorted(files):
        h.update(path.encode() + b"\0" + files[path].encode() + b"\0")
    return h.hexdigest()[:16]


def history_entry(validation: dict, files: dict[str, str]) -> dict:
    """What we remember about one validation."""
    log = validation.get("log", "")
    return {
        "ok": validation.get("ok", False),
        "gate": validation.get("gate", ""),
        "failing": failing_count(log) if validation.get("gate") == "tests" else None,
        "signature": error_signature(validation.get("gate", ""), log),
        "fingerprint": fingerprint(files),
    }


def score(entry: dict) -> tuple[int, int]:
    """Higher is better: how far through the gates we got, then how few tests fail."""
    gate = GATE_ORDER.index(entry["gate"]) if entry["gate"] in GATE_ORDER else 0
    failing = entry["failing"] if entry["failing"] is not None else 999
    return gate, -failing


def made_progress(entry: dict, earlier: list[dict]) -> bool:
    """Better than everything before it, or a brand-new error (so the previous one was fixed)."""
    if not earlier:
        return True
    if score(entry) > max(score(h) for h in earlier):
        return True
    return entry["signature"] not in {h["signature"] for h in earlier}


def stalls(history: list[dict]) -> int:
    """How many attempts in a row made no progress."""
    count = 0
    for i, entry in enumerate(history):
        count = 0 if made_progress(entry, history[:i]) else count + 1
    return count


def repair_rung(history: list[dict], llm_available: bool) -> str | None:
    """Which repair to try next: 'cheap', 'strong', 'template', or None when the ladder is used up."""
    ladder = LADDER_WITH_LLM if llm_available else LADDER_OFFLINE
    n = stalls(history)
    return ladder[n] if n < len(ladder) else None


def should_continue(
    history: list[dict], tokens_used: int, token_budget: int, llm_available: bool, safety_limit: int
) -> tuple[bool, str]:
    """Decide whether another repair attempt is worth it. Returns (continue?, reason)."""
    if not history:
        return True, "nothing validated yet"
    attempts = len(history) - 1
    if attempts >= safety_limit:
        return False, f"safety limit of {safety_limit} repair attempts reached"
    if tokens_used >= token_budget:
        return False, f"token budget used up ({tokens_used} of {token_budget})"
    last = history[-1]
    if any(h["fingerprint"] == last["fingerprint"] for h in history[:-1]):
        return False, "going in circles: the code is back to an earlier version"
    rung = repair_rung(history, llm_available)
    if rung is None:
        tried = " -> ".join(LADDER_WITH_LLM if llm_available else LADDER_OFFLINE)
        return False, f"no progress after trying every repair level ({tried})"
    if stalls(history) == 0 and attempts > 0:
        return True, "progress: the last repair moved validation forward"
    return True, f"next repair level: {rung}"
