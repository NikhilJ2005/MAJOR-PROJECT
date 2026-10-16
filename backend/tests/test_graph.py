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


def test_edge_case_tests_find_a_bug_the_contract_tests_miss():
    """The model forgets to turn constraint errors into 409: the spec-derived edge-case test notices."""
    fake = FakeLLM(mistake="swallow_fk")
    values = _run(initial_state("blog", {"codegen_mode": "llm"}), fake)
    assert values["status"] == "succeeded", values.get("report")
    found = [b for b in values["bugs"] if b["found_by"] == "edge-case tests"]
    assert found and all(b["fixed"] for b in found)
    assert "reflect:cheap" in fake.calls


def test_escalates_only_when_stalled_cheap_then_strong_then_templates():
    """A reflector whose fixes never work: each stall climbs one rung."""
    fake = FakeLLM(mistake="typo", fix_reflection=False)
    values = _run(initial_state("blog", {"codegen_mode": "llm"}), fake)
    assert [c for c in fake.calls if c.startswith("reflect")] == ["reflect:cheap", "reflect:strong"]
    assert values["status"] == "succeeded"
    assert values["iteration"] == 3
    assert any(e["agent"] == "template_repair" for e in values["ledger"])


def test_stops_when_going_in_circles(monkeypatch):
    """Templates are already in place, so a repair changes nothing: stop instead of looping."""

    class AlwaysFails:
        def validate(self, files, stack="fastapi"):
            return GateResult(False, "tests", "FAILED tests/test_smoke.py::test_crud[Post] - assert 500 == 201\n1 failed", 0.1)

    monkeypatch.setattr(sandbox_mod, "get_sandbox", lambda: AlwaysFails())
    monkeypatch.setattr("agent.nodes.get_sandbox", lambda: AlwaysFails())
    values = _run(initial_state("blog", {"codegen_mode": "template"}, BLOG_SPEC))
    assert values["status"] == "failed"
    assert values["iteration"] == 1
    assert "circles" in values["stop_reason"]
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
    assert fake.calls.index("rule_tests") < fake.calls.index("entity")  # tests before code
    migrations = [p for p in values["files"] if "migrations/" in p and p.endswith(".py") and "env.py" not in p]
    assert migrations, "packager should ship real migration files"
