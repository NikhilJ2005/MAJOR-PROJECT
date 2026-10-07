import ast

import pytest

from agent.codegen import inject_fault, plan_files, render_project
from agent.sandbox import LocalSandbox
from agent.state import ProjectSpec

from agent.fake_llm import BLOG_SPEC

NO_AUTH = {
    "project_name": "inventory",
    "entities": [
        {"name": "Warehouse", "fields": [{"name": "name", "unique": True}, {"name": "capacity", "type": "int", "required": False}]},
        {"name": "Item", "fields": [{"name": "sku"}, {"name": "price", "type": "float"}, {"name": "active", "type": "bool"},
                                     {"name": "restock_at", "type": "datetime", "required": False}, {"name": "warehouse_id", "references": "Warehouse"}]},
    ],
}


@pytest.mark.parametrize("raw", [BLOG_SPEC, NO_AUTH], ids=["auth", "no-auth"])
def test_rendered_project_is_valid_python_and_matches_plan(raw):
    spec = ProjectSpec.model_validate(raw)
    files = render_project(spec)
    assert {f["path"] for f in plan_files(spec)} == set(files)
    for path, src in files.items():
        if path.endswith(".py"):
            ast.parse(src, filename=path)


@pytest.mark.parametrize("raw", [BLOG_SPEC, NO_AUTH], ids=["auth", "no-auth"])
def test_rendered_project_passes_all_gates(raw):
    result = LocalSandbox().validate(render_project(ProjectSpec.model_validate(raw)))
    assert result.ok, result.log


def test_injected_fault_fails_import_gate():
    spec = ProjectSpec.model_validate(NO_AUTH)
    files = render_project(spec)
    path, broken, _ = inject_fault(spec, files, "import")
    result = LocalSandbox().validate({**files, path: broken})
    assert not result.ok and result.gate == "import"
    assert "Sesion" in result.log


def test_status_fault_fails_contract_tests_and_is_located():
    from agent.healing import classify_error, locate_failing_file

    spec = ProjectSpec.model_validate(NO_AUTH)
    files = render_project(spec)
    path, broken, _ = inject_fault(spec, files, "status")
    assert "HTTP_200_OK" in broken
    result = LocalSandbox().validate({**files, path: broken})
    assert not result.ok and result.gate == "tests", result.log
    assert classify_error(result.log) == "contract"
    assert locate_failing_file(result.log, files, spec) == path


def test_import_fault_planted_even_if_router_was_rewritten():
    spec = ProjectSpec.model_validate(NO_AUTH)
    files = {f"app/routers/{spec.entities[0].module}.py": "from fastapi import APIRouter\nrouter = APIRouter()\n"}
    _, broken, _ = inject_fault(spec, files, "status")  # no 201 to break -> falls back to import bug
    assert broken.startswith("from sqlalchemy.orm import Sesion")


def test_sandbox_does_not_leak_secrets(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-super-secret")
    files = render_project(ProjectSpec.model_validate(NO_AUTH))
    files["app/main.py"] += '\nimport os\nassert "OPENROUTER_API_KEY" not in os.environ, "key leaked"\n'
    assert LocalSandbox().validate(files).ok


@pytest.mark.skipif(LocalSandbox().identity is None, reason="needs root + a `sandbox` OS user (as in the Docker image)")
def test_generated_code_cannot_read_server_environment():
    files = render_project(ProjectSpec.model_validate(NO_AUTH))
    files["app/main.py"] += (
        "\nimport os\n"
        "try:\n"
        "    open(f'/proc/{os.getppid()}/environ').read()\n"
        "    raise SystemExit('LEAK: parent environment readable')\n"
        "except PermissionError:\n"
        "    pass\n"
    )
    result = LocalSandbox().validate(files)
    assert result.ok, result.log
