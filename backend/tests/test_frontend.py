import json

from agent import llm as llm_mod
from agent.codegen import render_project
from agent.fake_llm import FakeLLM
from agent.frontend import CONFIG, UI, check_frontend
from agent.graph import build_graph, initial_state
from agent.healing import classify_error, locate_failing_file
from agent.state import ProjectSpec
from tests.specs import WORKFLOW

SPEC = ProjectSpec.model_validate(WORKFLOW)


def test_rendered_frontend_matches_the_backend():
    files = render_project(SPEC)
    assert check_frontend(files, SPEC) == []
    config = json.loads(files[CONFIG])
    assert [e["table"] for e in config["entities"]] == ["agent_nodes", "task_graphs", "execution_logs"]
    assert "vendor/react.production.min.js" in files["app/static/index.html"]
    assert json.loads(files[UI])["entities"]["AgentNode"]["icon"] == "🤖"


def test_contract_check_catches_frontend_backend_mismatches():
    files = render_project(SPEC)
    ui = json.loads(files[UI])
    ui["dashboard"] = [{"title": "Robots", "entity": "Robot"}, {"title": "By mood", "entity": "AgentNode", "group_by": "mood"}]
    problems = check_frontend({**files, UI: json.dumps(ui)}, SPEC)
    assert len(problems) == 2 and all("FrontendContractError" in p for p in problems)
    log = "\n".join(problems)
    assert classify_error(log) == "frontend"
    assert locate_failing_file(log, files, SPEC) == UI
    assert check_frontend({**files, UI: "{not json"}, SPEC)[0].startswith(f"{UI}:1: FrontendContractError")


def test_a_broken_ui_design_is_found_and_healed():
    class BadDesigner(FakeLLM):
        def complete(self, role, system, user):
            if "web app's look" in system:
                bad = json.loads(user.split("Default (improve on it):\n", 1)[1])
                bad["dashboard"].append({"title": "Ghosts", "entity": "Ghost"})
                return json.dumps(bad), super().complete(role, "Reflector", "File most likely at fault: x\n```python\n```")[1]
            return super().complete(role, system, user)

    llm_mod.set_llm(BadDesigner(spec=WORKFLOW))
    try:
        graph = build_graph()
        state = initial_state("agents", {"codegen_mode": "llm"})
        config = {"configurable": {"thread_id": state["run_id"]}}
        list(graph.stream(state, config, stream_mode="updates"))
        values = graph.get_state(config).values
    finally:
        llm_mod.set_llm(None)
    assert values["status"] == "succeeded", values.get("report")
    assert [b["found_by"] for b in values["bugs"]] == ["frontend check"]
    assert "Ghost" not in values["files"][UI]
