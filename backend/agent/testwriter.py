"""Tests written by AI agents, and the rules that keep them honest.

- The Test Engineer writes tests/test_rules.py from the requirements *before* any code exists.
- The Bug Hunter (bughunt.py) writes tests/test_edge_cases.py after the contract passes.
- If the Reflector believes one of these tests is wrong, the Judge decides; a test ruled wrong is
  removed (and logged), the contract tests in tests/test_smoke.py can never be removed.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from . import prompts
from .analysis import requirements_text
from .llm import LLMError, Usage, parse_file_blocks
from .state import ProjectSpec

RULE_TESTS = "tests/test_rules.py"
EDGE_TESTS = "tests/test_edge_cases.py"
AI_TEST_FILES = (RULE_TESTS, EDGE_TESTS)


def valid_test_file(source: str) -> bool:
    """Parses as Python and defines at least one test function."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    return any(isinstance(n, ast.FunctionDef) and n.name.startswith("test_") for n in tree.body)


def write_rule_tests(llm: Any, spec: ProjectSpec) -> tuple[str | None, Usage]:
    """Ask the Test Engineer for tests/test_rules.py. None if the reply is unusable."""
    system = prompts.TEST_ENGINEER_SYSTEM.format(helpers=prompts.TEST_HELPERS, contract=prompts.CONTRACT)
    user = prompts.TEST_ENGINEER_USER.format(
        requirements=requirements_text(spec.requirements),
        spec=spec.model_dump_json(indent=1, exclude={"requirements"}),
    )
    text, usage = llm.complete("strong", system, user)
    source = parse_file_blocks(text).get(RULE_TESTS)
    return (source if source and valid_test_file(source) else None), usage


def needs_rule_tests(spec: ProjectSpec) -> bool:
    """Only worth it when there is something beyond plain CRUD to test."""
    return bool(spec.actions) or any(r.kind in ("rule", "permission", "endpoint") for r in spec.requirements)


def get_test_source(source: str, name: str) -> str | None:
    """Source code of one test function (`test_x[param]` finds `test_x`)."""
    name = name.split("[", 1)[0]
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(source, node)
    return None


def remove_test(source: str, name: str) -> str:
    """The file without one test function (and its decorators)."""
    name = name.split("[", 1)[0]
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            start = min([d.lineno for d in node.decorator_list] + [node.lineno]) - 1
            return "".join(lines[:start] + lines[node.end_lineno :])
    return source


def parse_dispute(text: str) -> tuple[str, str] | None:
    """`DISPUTE: test_name: reason` from a Reflector reply."""
    m = re.search(r"DISPUTE:\s*(test_[\w\[\]\-.]+)\s*:\s*(.+)", text)
    return (m.group(1), m.group(2).strip()) if m else None


def judge(llm: Any, spec: ProjectSpec, path: str, test: str, test_code: str, argument: str, log: str) -> tuple[bool, str, Usage]:
    """Strong model decides a dispute. Returns (test_is_wrong, reason, usage)."""
    system = prompts.JUDGE_SYSTEM.format(contract=prompts.CONTRACT)
    user = prompts.JUDGE_USER.format(
        requirements=requirements_text(spec.requirements), test=test, path=path,
        test_code=test_code, argument=argument, log=log[-3000:],
    )
    try:
        text, usage = llm.complete("strong", system, user)
    except LLMError as exc:
        return False, f"judge unavailable ({exc})", Usage()
    m = re.search(r"VERDICT:\s*(TEST_WRONG|CODE_WRONG)\s*:?\s*(.*)", text)
    if not m:
        return False, "judge gave no clear verdict; the test stays", usage
    return m.group(1) == "TEST_WRONG", m.group(2).strip(), usage
