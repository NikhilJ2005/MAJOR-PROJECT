import ast

import pytest

from agent.bughunt import CHECKS, failing_tests, static_check, update_bugs
from agent.codegen import render_project
from agent.state import ProjectSpec
from tests.specs import ALL_SPECS

BAD = {
    "dangerous-call": "import os\nos.system('rm -rf /')\n",
    "hardcoded-secret": "SECRET_KEY = 'super-secret-value'\n",
    "sql-injection": "def f(db, name):\n    db.execute(f\"SELECT * FROM users WHERE name = '{name}'\")\n",
    "debug-on": "DEBUG = True\n",
    "bare-except": "try:\n    pass\nexcept:\n    pass\n",
    "print": "print('hello')\n",
}
GOOD = {
    "dangerous-call": "import json\njson.loads('{}')\n",
    "hardcoded-secret": "import os\nSECRET_KEY = os.getenv('SECRET_KEY', '')\n",
    "sql-injection": "def f(db, name):\n    db.execute('SELECT * FROM users WHERE name = :n', {'n': name})\n",
    "debug-on": "import os\nDEBUG = os.getenv('DEBUG') == '1'\n",
    "bare-except": "try:\n    pass\nexcept ValueError:\n    pass\n",
    "print": "import logging\nlogging.info('hello')\n",
}


@pytest.mark.parametrize("rule", sorted(BAD))
def test_each_check_catches_the_bad_snippet_only(rule):
    bad = [f.rule for check in CHECKS for f in check("app/x.py", ast.parse(BAD[rule]))]
    good = [f.rule for check in CHECKS for f in check("app/x.py", ast.parse(GOOD[rule]))]
    assert rule in bad
    assert rule not in good


def test_hardcoded_getenv_fallback_is_caught():
    src = "import os\nKEY = os.getenv('SECRET_KEY', 'change-me-in-production')\n"
    assert [f.rule for f in static_check({"app/config.py": src})] == ["hardcoded-secret"]


def test_tests_and_migrations_are_not_checked():
    assert static_check({"tests/test_x.py": "print(1)\n", "app/migrations/0001.py": "print(1)\n"}) == []


@pytest.mark.parametrize("stack", ["fastapi", "flask", "django"])
@pytest.mark.parametrize("name", sorted(ALL_SPECS))
def test_templates_have_no_blocking_findings(stack, name):
    files = render_project(ProjectSpec.model_validate({**ALL_SPECS[name], "stack": stack}))
    assert [f.log_line() for f in static_check(files) if f.severity == "high"] == []


def test_bug_list_tracks_who_found_what_and_marks_fixes():
    log = ("FAILED tests/test_edge_cases.py::test_unknown_fk - assert 500 in (409, 422)\n"
           "FAILED tests/test_smoke.py::test_crud[Post] - assert 200 == 201\n2 failed")
    assert failing_tests(log) == [("tests/test_edge_cases.py", "test_unknown_fk"), ("tests/test_smoke.py", "test_crud[Post]")]
    bugs = update_bugs([], {"ok": False, "gate": "tests", "log": log})
    assert [(b["found_by"], b["fixed"]) for b in bugs] == [("edge-case tests", False), ("contract tests", False)]
    bugs = update_bugs(bugs, {"ok": False, "gate": "tests", "log": log})
    assert len(bugs) == 2  # the same failure is not counted twice
    bugs = update_bugs(bugs, {"ok": True, "gate": "tests", "log": "5 passed"})
    assert all(b["fixed"] for b in bugs)
