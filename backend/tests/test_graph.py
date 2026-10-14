from agent import llm as llm_mod
from agent import sandbox as sandbox_mod
from agent.fake_llm import BLOG_SPEC, FakeLLM
from agent.graph import build_graph, initial_state
from agent.sandbox import GateResult


def _run(graph, state):
    config = {"configurable": {"thread_id": state["run_id"]}}
    list(graph.stream(state, config, stream_mode="updates"))
    return graph.get_state(config).values


def _with_llm(fake):
    llm_mod.set_llm(fake)
    return fake


def test_model_mistake_is_found_and_healed():
    """Nobody plants a bug: the (fake) model makes a typo, the validator finds it, the cheap model fixes it."""
    fake = _with_llm(FakeLLM(buggy=True))
    try:
        values = _run(build_graph(), initial_state("a blog with posts and comments", {"codegen_mode": "llm"}))
    finally:
        llm_mod.set_llm(None)

    assert values["status"] == "succeeded"
    assert values["iteration"] == 1
    agents = [e["agent"] for e in values["ledger"]]
    assert "fault_injector" not in agents
    assert agents.index("error_classifier") < agents.index("reflector (cheap)")
    assert "reflect:cheap" in fake.calls
    reflector = next(e for e in values["ledger"] if e["agent"].startswith("reflector"))
    diff = "\n".join(reflector["diff"].values())
    assert "-from sqlalchemy.orm import Sesion" in diff and "+from sqlalchemy.orm import Session" in diff
    assert [h["ok"] for h in values["heal_history"]] == [False, True]


def test_clean_run_needs_no_healing(fake_llm):
    values = _run(build_graph(), initial_state("blog", {"codegen_mode": "llm"}))
    assert values["status"] == "succeeded"
    assert values["iteration"] == 0
    assert not any(c.startswith("reflect") for c in fake_llm.calls)


def test_escalates_only_when_stalled_cheap_then_strong_then_templates():
    """A reflector whose fixes never work: each stall climbs one rung."""
    fake = _with_llm(FakeLLM(buggy=True, fix_reflection=False))
    try:
        values = _run(build_graph(), initial_state("blog", {"codegen_mode": "llm"}))
    finally:
        llm_mod.set_llm(None)
    assert [c for c in fake.calls if c.startswith("reflect")] == ["reflect:cheap", "reflect:strong"]
    assert values["status"] == "succeeded"
    assert values["iteration"] == 3
    assert any(e["agent"] == "template_repair" for e in values["ledger"])


def test_stops_when_going_in_circles(offline, monkeypatch):
    """Templates are already in place, so a repair changes nothing: stop instead of looping."""

    class AlwaysFails:
        def validate(self, files):
            return GateResult(False, "tests", "FAILED tests/test_smoke.py::test_crud[Post] - assert 500 == 201\n1 failed", 0.1)

    monkeypatch.setattr(sandbox_mod, "get_sandbox", lambda: AlwaysFails())
    monkeypatch.setattr("agent.nodes.get_sandbox", lambda: AlwaysFails())
    values = _run(build_graph(), initial_state("blog", {"codegen_mode": "template"}, BLOG_SPEC))
    assert values["status"] == "failed"
    assert values["iteration"] == 1
    assert "circles" in values["stop_reason"]
    assert values["ledger"][-1]["agent"] == "circuit_breaker"
    assert "circles" in values["report"]
