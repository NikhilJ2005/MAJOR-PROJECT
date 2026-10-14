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


# --------------------------------------------------------------------------- dynamic stopping

from agent.healing import error_signature, failing_count, repair_rung, should_continue  # noqa: E402


def _h(gate, failing=None, fp="a", ok=False):
    return {"ok": ok, "gate": gate, "failing": failing, "signature": gate, "fingerprint": fp}


def _go(history, llm=True, tokens=0):
    return should_continue(history, tokens_used=tokens, token_budget=1000, llm_available=llm, safety_limit=12)


def test_failing_count_reads_pytest_summary():
    assert failing_count("== 2 failed, 5 passed in 1.2s ==") == 2
    assert failing_count("== 1 failed, 1 error in 0.3s ==") == 2
    assert failing_count("== 7 passed in 1s ==") == 0
    assert failing_count("ImportError: boom") is None


def test_signature_ignores_line_numbers():
    a = error_signature("import", 'File "app/x.py", line 3\nImportError: cannot import name Foo')
    b = error_signature("import", 'File "app/x.py", line 9\nImportError: cannot import name Foo')
    assert a == b


def test_first_failure_tries_the_cheap_model():
    assert _go([_h("import")]) == (True, "next repair level: cheap")
    assert repair_rung([_h("import")], llm_available=True) == "cheap"


def test_progress_keeps_going_and_resets_to_cheap():
    history = [_h("import", fp="a"), _h("tests", failing=3, fp="b"), _h("tests", failing=1, fp="c")]
    keep, reason = _go(history)
    assert keep and reason.startswith("progress")
    assert repair_rung(history, True) == "cheap"


def test_each_stall_climbs_one_rung_then_stops():
    h = [_h("tests", 2, "a"), _h("tests", 2, "b")]
    assert repair_rung(h, True) == "strong"
    h.append(_h("tests", 2, "c"))
    assert repair_rung(h, True) == "template"
    h.append(_h("tests", 3, "d"))
    keep, reason = _go(h)
    assert not keep and "no progress" in reason


def test_offline_has_only_the_template_rung():
    assert repair_rung([_h("import")], llm_available=False) == "template"
    keep, _ = _go([_h("import", fp="a"), _h("import", fp="b")], llm=False)
    assert not keep


def test_circles_stop():
    keep, reason = _go([_h("tests", 2, "a"), _h("tests", 1, "b"), _h("tests", 2, "a")])
    assert not keep and "circles" in reason


def test_budget_stops():
    keep, reason = _go([_h("import")], tokens=5000)
    assert not keep and "budget" in reason


def test_safety_limit_is_only_a_seatbelt():
    history = [_h("tests", 100 - i, fp=str(i)) for i in range(13)]  # always improving
    keep, reason = _go(history)
    assert not keep and "safety limit" in reason
