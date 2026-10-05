from langgraph.types import Command

from agent import sandbox as sandbox_mod
from agent.graph import build_graph, initial_state
from agent.sandbox import GateResult

from agent.fake_llm import BLOG_SPEC


def _run(graph, state):
    config = {"configurable": {"thread_id": state["run_id"]}}
    list(graph.stream(state, config, stream_mode="updates"))
    return config


def test_human_approval_interrupt_then_self_heal_with_llm(fake_llm):
    graph = build_graph()
    state = initial_state("a blog with posts and comments", {"inject_fault": True, "codegen_mode": "llm"})
    config = _run(graph, state)

    snap = graph.get_state(config)
    assert snap.next == ("approve_spec",)  # paused for the human
    assert snap.values["spec"]["project_name"] == "blog_api"

    list(graph.stream(Command(resume={"approved": True}), config, stream_mode="updates"))
    values = graph.get_state(config).values

    assert values["status"] == "succeeded"
    assert values["iteration"] == 1
    agents = [e["agent"] for e in values["ledger"]]
    assert agents.index("fault_injector") < agents.index("error_classifier") < agents.index("reflector (cheap)")
    assert "reflect:cheap" in fake_llm.calls  # cheap model handles the first repair attempt
    assert values["usage"]["total_tokens"] > 0
    assert any(f["source"] == "llm" and f["severity"] == "low" for f in values["review"])


def test_edited_spec_is_used(fake_llm):
    graph = build_graph()
    config = _run(graph, initial_state("blog", {"codegen_mode": "template"}))
    edited = {**BLOG_SPEC, "entities": BLOG_SPEC["entities"][:1]}
    list(graph.stream(Command(resume={"approved": True, "spec": edited}), config, stream_mode="updates"))
    values = graph.get_state(config).values
    assert values["status"] == "succeeded"
    assert "app/routers/comment.py" not in values["files"]


def test_rejection_ends_run(fake_llm):
    graph = build_graph()
    config = _run(graph, initial_state("blog", {}))
    list(graph.stream(Command(resume={"approved": False, "reason": "wrong"}), config, stream_mode="updates"))
    assert graph.get_state(config).values["status"] == "rejected"


def test_offline_template_repair(offline):
    graph = build_graph()
    state = initial_state("blog", {"auto_approve": True, "inject_fault": True, "codegen_mode": "template"}, BLOG_SPEC)
    config = _run(graph, state)
    values = graph.get_state(config).values
    assert values["status"] == "succeeded"
    assert any(e["agent"] == "template_repair" for e in values["ledger"])


def test_circuit_breaker_trips(offline, monkeypatch):
    class AlwaysFails:
        def validate(self, files):
            return GateResult(False, "tests", "FAILED tests/test_smoke.py::test_crud[Post] - assert 500 == 201", 0.1)

    monkeypatch.setattr(sandbox_mod, "get_sandbox", lambda: AlwaysFails())
    monkeypatch.setattr("agent.nodes.get_sandbox", lambda: AlwaysFails())
    graph = build_graph()
    config = _run(graph, initial_state("blog", {"auto_approve": True, "codegen_mode": "template"}, BLOG_SPEC))
    values = graph.get_state(config).values
    assert values["status"] == "failed"
    assert values["iteration"] == 3
    assert values["ledger"][-1]["agent"] == "circuit_breaker"
    assert "Circuit breaker" in values["report"]


def test_escalation_cheap_then_strong_then_templates(monkeypatch):
    """A reflector that never fixes anything: cheap -> strong -> deterministic template restore."""
    from agent import llm as llm_mod
    from agent.fake_llm import FakeLLM

    fake = FakeLLM(fix_reflection=False)
    llm_mod.set_llm(fake)
    try:
        graph = build_graph()
        state = initial_state("blog", {"auto_approve": True, "inject_fault": True, "codegen_mode": "llm"})
        config = _run(graph, state)
        values = graph.get_state(config).values
    finally:
        llm_mod.set_llm(None)
    assert [c for c in fake.calls if c.startswith("reflect")] == ["reflect:cheap", "reflect:strong"]
    assert values["status"] == "succeeded"
    assert values["iteration"] == 3
    assert values["ledger"][-3]["agent"] == "template_repair"
