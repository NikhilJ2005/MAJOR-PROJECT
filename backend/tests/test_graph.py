import pytest

from agent import llm as llm_mod
from agent import sandbox as sandbox_mod
from agent.fake_llm import BLOG_SPEC, FakeLLM
from agent.graph import build_graph, initial_state
from agent.sandbox import GateResult
from tests.specs import SHOP


def _run(state, fake=None):
    llm_mod.set_llm(fake)
    try:
        graph = build_graph()
        config = {"configurable": {"thread_id": state["run_id"]}}
        list(graph.stream(state, config, stream_mode="updates"))
        return graph.get_state(config).values
    finally:
        llm_mod.set_llm(None)


def _agents(values):
    return [e["agent"] for e in values["ledger"]]


def test_graph_has_the_eight_original_agents():
    from agent.graph import NODE_ORDER

    assert NODE_ORDER == ["parse_spec", "plan", "generate", "validate", "classify", "reflect", "package", "failure_report"]


def test_model_mistake_is_found_and_healed():
    """Nobody plants a bug: the (fake) model makes a typo, the validator finds it, the cheap model fixes it."""
    fake = FakeLLM(mistake="typo")
    values = _run(initial_state("a blog with posts and comments", {"codegen_mode": "llm"}), fake)

    assert values["status"] == "succeeded"
    assert values["iteration"] == 1
    agents = _agents(values)
    assert agents[0] == "architect"
    assert agents.index("error_classifier") < agents.index("reflector (cheap)")
    reflector = next(e for e in values["ledger"] if e["agent"].startswith("reflector"))
    diff = "\n".join(reflector["diff"].values())
    assert "-from app.errors import ApiErorr" in diff and "+from app.errors import ApiError" in diff
    assert [b["found_by"] for b in values["bugs"]] == ["validator"]
    assert all(b["fixed"] for b in values["bugs"])


def test_clean_run_needs_no_healing():
    fake = FakeLLM()
    values = _run(initial_state("blog", {"codegen_mode": "llm"}), fake)
    assert values["status"] == "succeeded"
    assert values["iteration"] == 0
    assert not any(c.startswith("reflect") for c in fake.calls)
    assert "tests/test_edge_cases.py" in values["files"]
    assert values["spec"]["requirements"]  # the Architect lists requirements itself


def test_rule_tests_written_before_the_code_find_a_business_logic_bug():
    """The model gets an action's answer wrong: only the Architect's rule test, written first, notices."""
    fake = FakeLLM(mistake="wrong_rule")
    values = _run(initial_state("blog", {"codegen_mode": "llm"}), fake)
    assert values["status"] == "succeeded", values.get("report")
    found = [b for b in values["bugs"] if b["found_by"] == "rule tests"]
    assert found and all(b["fixed"] for b in found)
    assert "reflect:cheap" in fake.calls
    reflector = next(e for e in values["ledger"] if e["agent"].startswith("reflector"))
    assert list(reflector["diff"]) == ["app/services/post.py"]


def test_only_the_business_logic_is_written_by_the_model():
    """Models, schemas and routers stay verified template code; one call per entity with actions."""
    fake = FakeLLM()
    values = _run(initial_state("blog", {"codegen_mode": "llm"}), fake)
    assert values["status"] == "succeeded"
    assert fake.calls.count("entity") == 1  # Post has the only action; Comment needs no call
    templates = values["template_files"]
    changed = sorted(p for p in templates if p.startswith("app/") and values["files"][p] != templates[p])
    assert changed == ["app/services/post.py", "app/static/ui.json"] or changed == ["app/services/post.py"]


def test_rule_tests_that_contradict_the_verified_contract_are_removed(monkeypatch):
    """Calibration: a CRUD rule test the verified templates fail is wrong; action tests are kept."""
    from agent import testwriter

    wrong = (
        "def test_list_is_never_empty(client, user_headers):\n"
        "    assert client.get('/posts/', headers=user_headers).status_code == 418\n\n\n"
        "def test_stats_counts(client, user_headers):\n"
        "    assert client.get('/posts/stats', headers=user_headers).json()['count'] >= 0\n"
    )
    monkeypatch.setattr("agent.nodes.write_rule_tests", lambda llm, spec, stream=None: (wrong, llm_mod.Usage()))
    values = _run(initial_state("blog", {"codegen_mode": "llm"}), FakeLLM())
    assert values["status"] == "succeeded", values.get("report")
    rules = values["files"]["tests/test_rules.py"]
    assert "test_list_is_never_empty" not in rules and "test_stats_counts" in rules
    assert any("contradict the verified contract" in e["action"] for e in values["ledger"])
    assert testwriter.touches_action("client.get('/posts/stats')", testwriter.ProjectSpec.model_validate(BLOG_SPEC))


def test_escalates_only_when_stalled_cheap_then_strong_then_templates():
    """A reflector whose fixes never work: each stall climbs one rung."""
    fake = FakeLLM(mistake="typo", fix_reflection=False)
    values = _run(initial_state("blog", {"codegen_mode": "llm"}), fake)
    repairs = [e["agent"] for e in values["ledger"] if e["agent"].startswith(("reflector", "template"))]
    assert repairs[:3] == ["reflector (cheap)", "reflector (strong)", "template_repair"]
    # The templates only hold stubs for business logic: the app starts, but its action rule still fails,
    # so healing stops honestly instead of claiming success.
    assert values["status"] == "failed"
    assert values["stop_reason"]


def test_stops_when_no_repair_level_helps(monkeypatch):
    """Templates are already in place, so a repair changes nothing: stop instead of looping."""

    class AlwaysFails:
        def validate(self, files, stack="fastapi"):
            return GateResult(False, "tests", "FAILED tests/test_smoke.py::test_crud[Post] - assert 500 == 201\n1 failed", 0.1)

    monkeypatch.setattr(sandbox_mod, "get_sandbox", lambda: AlwaysFails())
    monkeypatch.setattr("agent.nodes.get_sandbox", lambda: AlwaysFails())
    values = _run(initial_state("blog", {"codegen_mode": "template"}, BLOG_SPEC))
    assert values["status"] == "failed"
    assert values["iteration"] == 1
    assert "no progress" in values["stop_reason"]
    assert values["ledger"][-1]["agent"] == "circuit_breaker"


@pytest.mark.parametrize("stack", ["fastapi", "flask", "django"])
def test_complex_spec_end_to_end_on_every_framework(stack):
    """Requirements, owner/admin access, actions and rule tests, for each framework."""
    fake = FakeLLM(spec=SHOP)
    values = _run(initial_state("a shop", {"codegen_mode": "llm", "stack": stack}), fake)
    assert values["status"] == "succeeded", values.get("report")
    assert values["spec"]["stack"] == stack
    assert len(values["spec"]["requirements"]) == 3
    assert "tests/test_rules.py" in values["files"]  # the shop has actions, so the Architect wrote rule tests
    assert "rule_tests" in fake.calls and "entity" in fake.calls  # tests written from the design, beside the code
    migrations = [p for p in values["files"] if "migrations/" in p and p.endswith(".py") and "env.py" not in p]
    assert migrations, "packager should ship real migration files"


def test_sandbox_limit_is_retried_not_repaired(monkeypatch):
    """`can't start new thread` is the server's limit, not a bug: no code change, no bug, validate again."""
    from agent import nodes
    from agent.sandbox import LocalSandbox

    real = LocalSandbox()
    calls = []

    class Busy:
        def validate(self, files, stack="fastapi"):
            calls.append(1)
            if len(calls) == 1:
                return GateResult(False, "boot", "RuntimeError: can't start new thread\nTimeoutError: /health did not respond", 0.1)
            return real.validate(files, stack)

        def make_migrations(self, files, stack):
            return real.make_migrations(files, stack)

    monkeypatch.setattr(nodes, "get_sandbox", lambda: Busy())
    monkeypatch.setattr(nodes, "SANDBOX_RETRY_WAIT_S", 0)
    values = _run(initial_state("blog", {"codegen_mode": "template"}, BLOG_SPEC))
    assert values["status"] == "succeeded"
    assert values["error_class"] == "sandbox"
    retry = next(e for e in values["ledger"] if e["agent"] == "reflector")
    assert "sandbox limit" in retry["action"] and "diff" not in retry
    assert values["bugs"] == []


def test_a_syntax_error_is_fixed_before_validation():
    """The model breaks Python syntax; one quick fix call repairs it, so validation passes on the first try."""
    fake = FakeLLM(mistake="syntax")
    values = _run(initial_state("blog", {"codegen_mode": "llm"}), fake)
    assert values["status"] == "succeeded", values.get("report")
    assert "syntax_fix" in fake.calls
    assert values["iteration"] == 0  # no repair round was needed
    wrote = next(e for e in values["ledger"] if e["action"].startswith("wrote the business logic of Post"))
    assert "fixed a syntax error before validation" in wrote["rationale"]


def test_rule_tests_and_business_logic_run_at_the_same_time(monkeypatch):
    """The Architect's rule tests are written in parallel with the code, not before it."""
    import threading
    import time as clock

    from agent import nodes

    inside = {"now": 0, "most": 0}
    lock = threading.Lock()

    def slow(original):
        def wrapper(*args, **kwargs):
            with lock:
                inside["now"] += 1
                inside["most"] = max(inside["most"], inside["now"])
            clock.sleep(0.5)
            try:
                return original(*args, **kwargs)
            finally:
                with lock:
                    inside["now"] -= 1
        return wrapper

    monkeypatch.setattr(nodes, "_rule_tests", slow(nodes._rule_tests))
    monkeypatch.setattr(nodes, "_generate_service", slow(nodes._generate_service))
    values = _run(initial_state("blog", {"codegen_mode": "llm"}), FakeLLM())
    assert values["status"] == "succeeded"
    assert inside["most"] >= 2
