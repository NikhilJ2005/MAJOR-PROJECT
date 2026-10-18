"""Error classification and fault localisation for the self-healing loop.

Pure functions over the validation log, so they are cheap (no LLM tokens),
deterministic, and unit-testable.
"""

from __future__ import annotations

import hashlib
import re
from itertools import pairwise

from .state import ProjectSpec

SANDBOX_LIMIT = re.compile(
    r"can't start new thread|Resource temporarily unavailable|BlockingIOError: \[Errno 11\]|Too many open files"
)

# Order matters: the first matching rule wins.
_RULES: list[tuple[str, re.Pattern[str]]] = [
    # The sandbox itself ran out of threads, processes or files: not a bug in the generated code.
    ("sandbox", SANDBOX_LIMIT),
    ("frontend", re.compile(r"FrontendContractError")),
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


def editable(path: str) -> bool:
    """Files the AI may change: the business logic and the app's look. Everything else is verified template code."""
    return path.startswith("app/services/") and path.endswith(".py") or path == "app/static/ui.json"


def repair_target(path: str | None, files: dict[str, str]) -> str | None:
    """Point a failure inside verified template code at the entity's business logic, where the bug can be."""
    if path is None or editable(path):
        return path
    m = re.match(r"app/(?:routers|models|schemas)/(\w+\.py)$", path)
    service = f"app/services/{m.group(1)}" if m else None
    return service if service in files else path


def failing_targets(log: str, files: dict[str, str], spec: ProjectSpec, first: str | None, limit: int = 3) -> list[str]:
    """Up to `limit` distinct business-logic files with failing tests, starting with `first`.

    A complex app often fails in several independent places; repairing them in the same attempt keeps
    healing from needing one attempt per file.
    """
    targets = [first] if first and editable(first) else []
    for test_path, name in re.findall(r"^(?:FAILED|ERROR) (tests/[\w/]+\.py)::(\w+)", log, re.MULTILINE):
        if len(targets) >= limit:
            break
        if test_path not in files:
            continue
        target = repair_target(file_for_test(_function_source(files[test_path], name), spec), files)
        if target and editable(target) and target not in targets:
            targets.append(target)
    return targets


def guess_service(log: str, files: dict[str, str], spec: ProjectSpec) -> str | None:
    """When nothing in the log points at a file: the business-logic file whose entity the log mentions most."""
    counts = {}
    for e in spec.entities:
        path = f"app/services/{e.module}.py"
        if path in files and spec.actions_for(e):
            counts[path] = log.count(f"/{e.table}") + log.count(e.name)
    best = max(counts, key=counts.get, default=None)
    return best if best and counts[best] > 0 else None


def scaled_limits(spec: ProjectSpec, safety: int, budget_usd: float) -> tuple[int, float]:
    """Bigger apps get more repairs and more budget: the settings are for an app of up to 6 tables."""
    services = sum(1 for e in spec.entities if spec.actions_for(e))
    extra_tables = max(0, len(spec.entities) - 6)
    return max(safety, min(30, 6 + 2 * services)), budget_usd * (1 + extra_tables / 6)


def locate_failing_file(log: str, files: dict[str, str], spec: ProjectSpec) -> str | None:
    """Best guess at the generated file that holds the root cause."""
    # 0. The frontend contract check names the JSON file it is about.
    m = re.search(r"^(app/static/[\w.]+):\d+: FrontendContractError", log, re.MULTILINE)
    if m and m.group(1) in files:
        return m.group(1)
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
#   1. progress -> keep going, and drop back to the cheap model for the next (new) problem. Progress is
#      a better result than ever before, a better result than the attempt before (after a bad repair was
#      rolled back), some failing tests fixed, or a never-seen error because the previous one got fixed
#   2. no progress -> climb one rung of the ladder: cheap -> strong -> templates
#   3. top rung tried without progress                 -> stop
#   4. the same change between two versions happens again (circles) -> stop
#   5. budget spent                                     -> stop
#   6. the safety limit of attempts (a seatbelt, not the rule; it grows with the app) -> stop
# A validation that failed because the machine was busy (sandbox limits, a gate timeout without any test
# result) is "transient": it is retried without counting as a stall, at most MAX_TRANSIENT times in a row.
# A repair that makes things worse is rolled back to the best version (nodes.reflect), and a run that
# stops ships its best version, not its last.

GATE_ORDER = ["static", "import", "boot", "tests"]  # the order validation runs them
LADDER_WITH_LLM = ["cheap", "strong", "template"]
LADDER_OFFLINE = ["template"]
MAX_TRANSIENT = 3


def failing_count(log: str) -> int | None:
    """Number of failed tests in a pytest summary ("3 failed, 1 error"), None if no summary."""
    counts = [int(n) for n in re.findall(r"(\d+) (?:failed|errors?)\b", log)]
    if counts:
        return sum(counts)
    return 0 if re.search(r"\d+ passed", log) else None


def failed_tests(log: str) -> list[str]:
    """The ids of the failing tests in a pytest log ("tests/test_rules.py::test_refund"), sorted."""
    return sorted(set(re.findall(r"^(?:FAILED|ERROR) (tests/[\w/]+\.py::[\w\[\]\-. ]+?)(?: - |$)", log, re.MULTILINE)))


def transient(validation: dict) -> bool:
    """The machine was busy, not the code broken: sandbox limits, or a gate timeout with no test result at all."""
    log = validation.get("log", "")
    if SANDBOX_LIMIT.search(log):
        return True
    return "TimeoutError" in log and failing_count(log) is None and validation.get("gate") in ("boot", "tests")


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
    tests_gate = validation.get("gate") == "tests"
    return {
        "ok": validation.get("ok", False),
        "gate": validation.get("gate", ""),
        "failing": failing_count(log) if tests_gate else None,
        "failed_tests": failed_tests(log) if tests_gate else [],
        "signature": error_signature(validation.get("gate", ""), log),
        "fingerprint": fingerprint(files),
        "transient": transient(validation),
    }


def score(entry: dict) -> tuple[int, int]:
    """Higher is better: how far through the gates we got, then how few tests fail."""
    gate = GATE_ORDER.index(entry["gate"]) if entry["gate"] in GATE_ORDER else 0
    failing = entry["failing"] if entry["failing"] is not None else 999
    return gate, -failing


def _real(history: list[dict]) -> list[dict]:
    return [h for h in history if not h.get("transient")]


def made_progress(entry: dict, earlier: list[dict]) -> bool:
    """Did this validation move healing forward compared with the ones before it?"""
    earlier = _real(earlier)
    if not earlier:
        return True
    if score(entry) > max(score(h) for h in earlier):
        return True  # the best result so far
    previous = earlier[-1]
    if score(entry) > score(previous):
        return True  # better than the attempt before (e.g. a bad repair was rolled back and improved on)
    if score(entry) < score(previous):
        return False  # worse: never progress (the repair is rolled back)
    fixed = set(previous.get("failed_tests") or []) - set(entry.get("failed_tests") or [])
    if fixed and entry["gate"] == previous["gate"] == "tests":
        return True  # as many failures, but some old ones are fixed: a different problem now
    return entry["signature"] not in {h["signature"] for h in earlier}


def stalls(history: list[dict]) -> int:
    """How many attempts in a row made no progress (transient validations do not count either way)."""
    count = 0
    for i, entry in enumerate(history):
        if entry.get("transient"):
            continue
        count = 0 if made_progress(entry, history[:i]) else count + 1
    return count


def best_index(history: list[dict]) -> int | None:
    """The validation with the best result (the earliest one on ties), ignoring transient ones."""
    real = [i for i, h in enumerate(history) if not h.get("transient")]
    if not real:
        return None
    return max(real, key=lambda i: (score(history[i]), -i))


def regressed(history: list[dict]) -> bool:
    """The last (real) validation is worse than the best one: the last repair should be rolled back."""
    best = best_index(history)
    return bool(history) and best is not None and not history[-1].get("transient") and score(history[-1]) < score(history[best])


def trailing_transient(history: list[dict]) -> int:
    count = 0
    for entry in reversed(history):
        if not entry.get("transient"):
            break
        count += 1
    return count


def repair_rung(history: list[dict], llm_available: bool) -> str | None:
    """Which repair to try next: 'cheap', 'strong', 'template', or None when the ladder is used up."""
    ladder = LADDER_WITH_LLM if llm_available else LADDER_OFFLINE
    n = stalls(history)
    return ladder[n] if n < len(ladder) else None


def should_continue(
    history: list[dict], tokens_used: int, token_budget: int, llm_available: bool, safety_limit: int,
    budget_problem: str | None = None,
) -> tuple[bool, str]:
    """Decide whether another repair attempt is worth it. Returns (continue?, reason).

    budget_problem: why the money or token budget is used up (llm.budget_left), if it is. Every run
    still gets its first repair, so a build is never given up without one try at fixing it.
    """
    if not history:
        return True, "nothing validated yet"
    attempts = len(history) - 1
    if attempts >= safety_limit:
        return False, f"safety limit of {safety_limit} repair attempts reached"
    if attempts > 0 and budget_problem:
        return False, budget_problem
    if attempts > 0 and tokens_used >= token_budget:
        return False, f"token budget used up ({tokens_used} of {token_budget})"
    if trailing_transient(history) > MAX_TRANSIENT:
        return False, "the server kept running out of time or resources while testing (not a bug in your app); try again"
    if history[-1].get("transient"):
        return True, "the machine was busy: testing again"
    # Circles: the same change between two versions happens again. (Going back to an earlier version once
    # is fine, e.g. after a bad repair was rolled back; a repair that changed nothing is just a stall.)
    steps = [(a["fingerprint"], b["fingerprint"]) for a, b in pairwise(history) if a["fingerprint"] != b["fingerprint"]]
    if steps and history[-2]["fingerprint"] != history[-1]["fingerprint"] and steps[-1] in steps[:-1]:
        return False, "going in circles: the same change keeps coming back"
    rung = repair_rung(history, llm_available)
    if rung is None:
        tried = " -> ".join(LADDER_WITH_LLM if llm_available else LADDER_OFFLINE)
        return False, f"no progress after trying every repair level ({tried})"
    if stalls(history) == 0 and attempts > 0:
        return True, "progress: the last repair moved validation forward"
    return True, f"next repair level: {rung}"
