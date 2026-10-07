from agent import sandbox as sandbox_mod
from agent.fake_llm import BLOG_SPEC, FakeLLM
from agent.graph import build_graph, initial_state
from agent.sandbox import GateResult


def _run(graph, state):
    config = {"configurable": {"thread_id": state["run_id"]}}
    list(graph.stream(state, config, stream_mode="updates"))
    return graph.get_state(config).values


def test_architecture_to_working_app_with_self_heal(fake_llm):
    values = _run(build_graph(), initial_state("a blog with posts and comments", {"inject_fault": True, "codegen_mode": "llm"}))

    assert values["spec"]["project_name"] == "blog_api"
    assert values["status"] == "succeeded"
    assert values["iteration"] == 1
    assert values["validation"]["ok"]
    agents = [e["agent"] for e in values["ledger"]]
    assert agents[0] == "architect"
    assert agents.index("fault_injector") < agents.index("error_classifier") < agents.index("reflector (cheap)")
    assert "reflect:cheap" in fake_llm.calls  # cheap model handles the first repair attempt
    assert values["usage"]["total_tokens"] > 0
    reflector = next(e for e in values["ledger"] if e["agent"].startswith("reflector"))
    assert reflector["model"] == "cheap"  # FakeLLM reports the role as the model name
    assert "-from sqlalchemy.orm import Sesion" in reflector["diff"]["app/routers/post.py"]
    assert "+from sqlalchemy.orm import Session" in reflector["diff"]["app/routers/post.py"]


def test_status_bug_heals(fake_llm):
    values = _run(build_graph(), initial_state("blog", {"fault": "status", "codegen_mode": "template"}, BLOG_SPEC))
    assert values["status"] == "succeeded"
    assert values["iteration"] >= 1
    classify = next(e for e in values["ledger"] if e["agent"] == "error_classifier")
    assert "contract" in classify["action"] and "app/routers/post.py" in classify["action"]


def test_clean_run_needs_no_healing(fake_llm):
    values = _run(build_graph(), initial_state("blog", {"inject_fault": False, "codegen_mode": "llm"}))
    assert values["status"] == "succeeded"
    assert values["iteration"] == 0
    assert not any(c.startswith("reflect") for c in fake_llm.calls)


def test_offline_template_repair(offline):
    state = initial_state("blog", {"inject_fault": True, "codegen_mode": "template"}, BLOG_SPEC)
    values = _run(build_graph(), state)
    assert values["status"] == "succeeded"
    assert any(e["agent"] == "template_repair" for e in values["ledger"])


def test_escalation_cheap_then_strong_then_templates():
    """A reflector that never fixes anything: cheap -> strong -> deterministic template restore."""
    from agent import llm as llm_mod

    fake = FakeLLM(fix_reflection=False)
    llm_mod.set_llm(fake)
    try:
        values = _run(build_graph(), initial_state("blog", {"inject_fault": True, "codegen_mode": "llm"}))
    finally:
        llm_mod.set_llm(None)
    assert [c for c in fake.calls if c.startswith("reflect")] == ["reflect:cheap", "reflect:strong"]
    assert values["status"] == "succeeded"
    assert values["iteration"] == 3
    assert values["ledger"][-3]["agent"] == "template_repair"


def test_circuit_breaker_trips(offline, monkeypatch):
    class AlwaysFails:
        def validate(self, files):
            return GateResult(False, "tests", "FAILED tests/test_smoke.py::test_crud[Post] - assert 500 == 201", 0.1)

    monkeypatch.setattr(sandbox_mod, "get_sandbox", lambda: AlwaysFails())
    monkeypatch.setattr("agent.nodes.get_sandbox", lambda: AlwaysFails())
    values = _run(build_graph(), initial_state("blog", {"codegen_mode": "template"}, BLOG_SPEC))
    assert values["status"] == "failed"
    assert values["iteration"] == 3
    assert values["ledger"][-1]["agent"] == "circuit_breaker"
    assert "Circuit breaker" in values["report"]
