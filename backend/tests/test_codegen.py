import ast

import pytest

from agent.codegen import plan_files, render_project
from agent.sandbox import LocalSandbox
from agent.state import ProjectSpec
from tests.specs import ALL_SPECS, NO_AUTH

STACKS = ["fastapi", "flask", "django"]


@pytest.mark.parametrize("stack", STACKS)
@pytest.mark.parametrize("name", sorted(ALL_SPECS))
def test_rendered_project_is_valid_python_and_matches_plan(stack, name):
    spec = ProjectSpec.model_validate({**ALL_SPECS[name], "stack": stack})
    files = render_project(spec)
    assert {f["path"] for f in plan_files(spec)} == set(files)
    for path, src in files.items():
        if path.endswith(".py"):
            ast.parse(src, filename=path)


@pytest.mark.parametrize("stack", STACKS)
@pytest.mark.parametrize("name", sorted(ALL_SPECS))
def test_rendered_project_passes_all_gates(stack, name):
    """The same spec-derived contract tests pass on every framework."""
    result = LocalSandbox().validate(render_project(ProjectSpec.model_validate({**ALL_SPECS[name], "stack": stack})), stack)
    assert result.ok, result.log
    assert "skipped" not in result.log.splitlines()[-1] or "passed" in result.log.splitlines()[-1]


@pytest.mark.parametrize("stack", STACKS)
def test_migrations_are_generated_and_apply(stack):
    files = render_project(ProjectSpec.model_validate({**ALL_SPECS["shop"], "stack": stack}))
    new, note = LocalSandbox().make_migrations(files, stack)
    assert new, note
    assert any("orders" in body for body in new.values())


def test_contract_violation_fails_tests_gate_and_is_located():
    """A router that answers 200 instead of 201 is caught by the spec-derived tests."""
    from agent.healing import classify_error, failing_count, locate_failing_file

    spec = ProjectSpec.model_validate(NO_AUTH)
    files = render_project(spec)
    path = "app/routers/warehouse.py"
    files[path] = files[path].replace("status.HTTP_201_CREATED", "status.HTTP_200_OK", 1)
    result = LocalSandbox().validate(files)
    assert not result.ok and result.gate == "tests", result.log
    assert classify_error(result.log) == "contract"
    assert locate_failing_file(result.log, files, spec) == path
    assert failing_count(result.log) >= 1


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


def test_memory_bomb_is_stopped_by_the_os_limit():
    """3 GB allocation in generated code fails with MemoryError instead of exhausting the server."""
    files = render_project(ProjectSpec.model_validate(NO_AUTH))
    files["app/main.py"] += "\nbomb = bytearray(3 * 1024 ** 3)\n"
    result = LocalSandbox().validate(files)
    assert not result.ok and result.gate == "import"
    assert "MemoryError" in result.log
