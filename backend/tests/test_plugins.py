import json

from agent import plugins
from agent.fake_llm import FakeLLM
from agent.graph import build_graph, initial_state
from agent.state import ProjectSpec
from tests.specs import SHOP


def test_example_plugins_are_discovered_with_their_hooks():
    found = {p["name"]: p["hooks"] for p in plugins.describe()}
    assert found["postman"] == ["extra_files"]
    assert found["kubernetes"] == ["extra_files"]
    assert found["todo_check"] == ["extra_checks"]
    assert found["strict_inputs"] == ["prompt_notes"]


def test_hooks_produce_files_findings_and_notes():
    chosen = plugins.chosen(["postman", "todo_check", "strict_inputs", "does_not_exist"])
    assert [name for name, _ in chosen] == ["postman", "todo_check", "strict_inputs"]
    spec = ProjectSpec.model_validate(SHOP)
    collection = json.loads(plugins.extra_files(chosen, spec)["postman"]["postman_collection.json"])
    assert any("checkout" in item["request"]["url"] for item in collection["item"])
    findings = plugins.extra_checks(chosen, {"app/x.py": "x = 1  # TODO: validate\n"})
    assert [(f.rule, f.severity) for f in findings] == [("todo-left", "low")]
    assert "whitespace" in plugins.prompt_notes(chosen, "fastapi")


def test_a_broken_plugin_never_breaks_a_run(tmp_path, monkeypatch):
    (tmp_path / "broken.py").write_text("def extra_files(spec):\n    raise RuntimeError('boom')\n")
    (tmp_path / "syntax.py").write_text("def oops(:\n")
    monkeypatch.setattr(plugins, "PLUGINS_DIR", tmp_path)
    chosen = plugins.chosen(["broken", "syntax"])
    assert [n for n, _ in chosen] == ["broken"]
    assert plugins.extra_files(chosen, ProjectSpec.model_validate(SHOP)) == {"broken": {}}


def test_plugin_files_ship_in_the_project():
    from agent import llm as llm_mod

    llm_mod.set_llm(FakeLLM())
    try:
        graph = build_graph()
        state = initial_state("blog", {"codegen_mode": "template", "plugins": ["kubernetes", "postman"]})
        config = {"configurable": {"thread_id": state["run_id"]}}
        list(graph.stream(state, config, stream_mode="updates"))
        values = graph.get_state(config).values
    finally:
        llm_mod.set_llm(None)
    assert values["status"] == "succeeded"
    assert {"k8s/deployment.yaml", "k8s/service.yaml", "postman_collection.json"} <= set(values["files"])
    assert any(row["owner"] == "plugin:kubernetes" for row in values["file_plan"])
