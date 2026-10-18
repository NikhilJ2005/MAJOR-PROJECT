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
    assert {"k8s/app.yaml", "postman_collection.json"} <= set(values["files"])
    assert any(row["owner"] == "plugin:kubernetes" for row in values["file_plan"])


NEW_PLUGINS = ["deploy_targets", "seed_data", "openapi_export", "secrets_scan"]


def _shop_with(names):
    from agent.codegen import render_project

    spec = ProjectSpec.model_validate({**SHOP, "stack": "flask"})
    files = render_project(spec)
    for added in plugins.extra_files(plugins.chosen(names), spec).values():
        files.update(added)
    return spec, files


def test_deploy_targets_and_openapi_files():
    import tomllib

    import yaml

    _, files = _shop_with(["deploy_targets", "openapi_export"])
    assert yaml.safe_load(files["render.yaml"])["services"][0]["healthCheckPath"] == "/health"
    assert tomllib.loads(files["fly.toml"])["http_service"]["internal_port"] == 8000
    assert json.loads(files["railway.json"])["build"]["builder"] == "DOCKERFILE"
    doc = json.loads(files["openapi.json"])
    assert doc["openapi"] == "3.1.0" and "/orders/{item_id}/checkout" in doc["paths"]
    assert doc["components"]["schemas"]["Product"]["properties"]["price"]["type"] == "number"


def test_secrets_scan_flags_real_looking_keys_only():
    checks = plugins.chosen(["secrets_scan"])
    _, clean = _shop_with([])
    assert plugins.extra_checks(checks, clean) == []  # the generated project itself is clean
    leaked = {"deploy.yaml": "env:\n  token: ghp_" + "a" * 36 + "\n", "notes.md": "AKIA" + "B" * 16}
    assert sorted(f.rule for f in plugins.extra_checks(checks, leaked)) == ["aws-access-key", "github-token"]


def test_seed_script_fills_a_running_app_through_its_api():
    """Start the generated shop (Flask) for real and run its scripts/seed.py against it."""
    import subprocess
    import sys

    from app.preview import PreviewManager

    spec, files = _shop_with(["seed_data"])
    previews = PreviewManager()
    try:
        p = previews.start("seedtest", files, "flask")
        script = p.workdir / "scripts" / "seed.py"
        out = subprocess.run([sys.executable, str(script), p.base_url, "--email", "admin@vibestack.dev"],
                             check=False, capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, out.stderr
        for e in spec.entities:
            assert f"{e.name}: 5 rows" in out.stdout, out.stdout
    finally:
        previews.stop_all()


def test_agents_choose_plugins_with_reasons():
    from agent.state import ProjectSpec
    from tests.specs import ESCROW

    spec = ProjectSpec.model_validate(ESCROW)
    choices = plugins.resolve({}, [{"name": "openapi_export", "reason": "partners integrate"}], spec,
                              "An escrow marketplace we deploy on Kubernetes")
    by_name = {c["name"]: c for c in choices}
    assert by_name["kubernetes"]["by"] == "planner" and "kubernetes" in by_name["kubernetes"]["reason"]
    assert by_name["openapi_export"] == {"name": "openapi_export", "by": "architect", "reason": "partners integrate"}
    assert by_name["secrets_scan"]["reason"] == "on for every build"
    assert "postman" in by_name  # wanted(spec): many custom actions


def test_user_overrides_and_switching_auto_off():
    from agent.state import ProjectSpec
    from tests.specs import ESCROW

    spec = ProjectSpec.model_validate(ESCROW)
    choices = plugins.resolve({"plugins_off": ["kubernetes"], "plugins": ["postman"]}, [], spec, "use k8s")
    names = [c["name"] for c in choices]
    assert "kubernetes" not in names and names.count("postman") == 1
    assert next(c for c in choices if c["name"] == "postman")["by"] == "user"
    assert [c["name"] for c in plugins.resolve({"auto_plugins": False, "plugins": ["postman"]}, [], spec, "k8s")] == ["postman"]
