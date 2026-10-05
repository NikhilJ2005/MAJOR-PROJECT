import pytest

from agent.healing import classify_error, locate_failing_file, related_files
from agent.state import ProjectSpec

SPEC = ProjectSpec.model_validate({"project_name": "x", "entities": [{"name": "Post", "fields": [{"name": "title"}]}]})
FILES = {p: "" for p in ["app/main.py", "app/routers/post.py", "app/models/post.py", "app/schemas/post.py", "app/database.py"]}


@pytest.mark.parametrize(
    "log,expected",
    [
        ("  File \"app/models/post.py\", line 3\n    x = (\nSyntaxError: '(' was never closed", "syntax"),
        ("ImportError: cannot import name 'Sesion' from 'sqlalchemy.orm'", "import"),
        ("ModuleNotFoundError: No module named 'passlib'", "dependency"),
        ("ModuleNotFoundError: No module named 'app.models.foo'", "import"),
        ("sqlalchemy.exc.NoReferencedTableError: Foreign key could not find table", "orm"),
        ("NameError: name 'Post' is not defined", "runtime"),
        ("FAILED tests/test_smoke.py::test_crud[Post] - assert 200 == 201", "contract"),
        ("TimeoutError: gate exceeded 60s", "timeout"),
        ("something odd", "unknown"),
    ],
)
def test_classify(log, expected):
    assert classify_error(log) == expected


def test_locate_prefers_deepest_app_frame():
    log = 'File "app/main.py", line 8, in <module>\n  File "app/routers/post.py", line 4, in <module>\nImportError: x'
    assert locate_failing_file(log, FILES, SPEC) == "app/routers/post.py"


def test_locate_from_failing_smoke_test():
    log = "FAILED tests/test_smoke.py::test_crud[Post] - assert 200 == 201"
    assert locate_failing_file(log, FILES, SPEC) == "app/routers/post.py"


def test_related_files_are_entity_siblings_only():
    assert related_files("app/routers/post.py", FILES) == ["app/models/post.py", "app/schemas/post.py", "app/database.py"]
