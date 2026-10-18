"""Deterministic stand-in for OpenRouter that plays every agent role.

Used by the test-suite, and by `FAKE_LLM=1` so the UI can be developed and
demoed without an API key or token spend. It never invents application code:
it implements custom actions with a simple row count. With `mistake=...` the Code
Generator makes a typical model slip, so tests can watch the agents find and repair a
bug that nobody planted in the pipeline:

    "typo"        a misspelled import in the business logic (caught when the app is imported)
    "wrong_rule"  an action answers with the wrong field (only the Architect's rule test,
                  written before the code, notices)
    "syntax"      a typo that breaks Python syntax (fixed by the syntax self-check before validation)
"""

from __future__ import annotations

import json
import os
import re
import time

from .llm import Usage, parse_file_blocks

BLOG_SPEC = {
    "project_name": "blog_api",
    "description": "A blog with posts and comments",
    "auth": True,
    "features": ["search"],
    "entities": [
        {"name": "Post", "fields": [{"name": "title"}, {"name": "body", "type": "text"}, {"name": "author_id", "references": "User"}]},
        {"name": "Comment", "fields": [{"name": "content", "type": "text"}, {"name": "post_id", "references": "Post"}]},
    ],
    "actions": [
        {"name": "stats", "entity": "Post", "method": "GET", "path": "/stats", "description": "How many posts exist",
         "rules": ['Returns {"count": <number of posts>}']},
    ],
}

MISTAKES = {
    "typo": ("from app.errors import ApiError", "from app.errors import ApiErorr"),
    "wrong_rule": ('return {"count":', 'return {"total":'),
    "syntax": ("def stats(", "def _ stats("),
}


def _section(text: str, start: str, end: str | None = None) -> str:
    body = text.split(start, 1)[1]
    return body.split(end, 1)[0] if end and end in body else body


class FakeLLM:
    """Plays the Architect (design + rule tests), Code Generator, Reflector and its judge step."""

    def __init__(self, spec=None, fix_reflection=True, mistake: str | None = None, buggy: bool = False, edit_spec=None):
        self.spec = spec or BLOG_SPEC
        self.edit_spec = edit_spec  # the design to return for a follow-up change request
        self.fix_reflection = fix_reflection
        self.mistake = MISTAKES.get(mistake or ("typo" if buggy else ""))
        self.mistake_name = mistake or ("typo" if buggy else None)
        self._made_typo = False
        self.calls: list[str] = []
        self.systems: list[tuple[str, str]] = []  # (role, system prompt) of every call, for tests

    # -- roles -------------------------------------------------------------------------

    def _architect(self, user: str) -> str:
        """The design, with requirements (one per entity when the test spec has none)."""
        spec = json.loads(json.dumps(self.edit_spec if self.edit_spec and "Change request:" in user else self.spec))
        if not spec.get("requirements"):
            spec["requirements"] = [
                {"id": f"R{i}", "text": f"Store and manage {e['name']} records", "kind": "data"}
                for i, e in enumerate(spec["entities"], 1)
            ]
            for i, e in enumerate(spec["entities"], 1):
                e.setdefault("requirement_ids", [f"R{i}"])
        return json.dumps(spec)

    def _service(self, system: str, user: str) -> str:
        """Implement every action stub as a row count (the "business logic" of the stand-in model)."""
        path, stub = re.search(r"The file to complete: (\S+)\n```python\n(.*?)```", user, re.DOTALL).groups()
        model = re.search(r"from app\.models\.\w+ import (\w+)", stub).group(1)
        count = f"{model}.objects.count()" if "Django ORM" in system else f"db.query({model}).count()"
        code = re.sub(r'raise ApiError\(501, "[^"]*"\)', f'return {{"count": {count}}}', stub)
        if self.mistake and self.mistake[0] in code and not self._made_typo:
            self._made_typo = True
            code = code.replace(*self.mistake, 1)
        return f"RATIONALE: counted the rows\n### FILE: {path}\n```python\n{code}```"

    def _first_entity(self, user: str) -> tuple[dict, dict]:
        from .codegen import smoke_cases
        from .state import ProjectSpec

        design = json.loads(_section(user, "Design (JSON):\n", "\n\nCode:"))
        spec = ProjectSpec.model_validate(design)
        cases = smoke_cases(spec)
        with_fk = [c for c, e in zip(cases, spec.entities) if e.foreign_keys]
        return (with_fk or cases)[0], design

    def _rule_tests(self, user: str) -> str:
        case, design = self._first_entity(user)
        tests = (
            "def test_list_respects_limit(client, user_headers):\n"
            f"    r = client.get('/{case['table']}/', params={{'limit': 1}}, headers=user_headers)\n"
            "    assert r.status_code == 200, r.text\n"
            "    assert len(r.json()) <= 1\n"
        )
        from .state import ProjectSpec

        spec = ProjectSpec.model_validate(design)
        tables = {e.name: e.table for e in spec.entities}
        for a in spec.actions:
            if a.method == "GET" and "{" not in a.path:
                tests += (
                    f"\n\ndef test_{a.name}_answers_with_a_count(client, user_headers):\n"
                    f"    r = client.get('/{tables[a.entity]}{a.path}', headers=user_headers)\n"
                    "    assert r.status_code == 200, r.text\n"
                    "    assert r.json()['count'] >= 0\n"
                )
        return f"### FILE: tests/test_rules.py\n```python\n{tests}```\n"

    def _reflect(self, user: str) -> str:
        m = re.search(r"File most likely at fault: (\S+)\n```python\n(.*?)```", user, re.DOTALL)
        path, content = m.group(1), m.group(2)
        if self.fix_reflection:
            for good, bad in MISTAKES.values():
                content = content.replace(bad, good)
        else:
            content += "\n# attempted fix (still broken)\n"
        return f"DIAGNOSIS: the code does not match the expected behaviour\n### FILE: {path}\n```python\n{content}```"

    def complete_with_tools(self, role, system, user, toolbox):
        """The Reflector with tools: read the file, stage the fix, run the failing tests, answer."""
        text, usage = self.complete(role, system, user)
        path = re.search(r"File most likely at fault: (\S+)", user)
        if path:
            toolbox.call("read_file", {"path": path.group(1)})
        for p, body in parse_file_blocks(text).items():
            toolbox.call("write_file", {"path": p, "content": body})
        failing = re.findall(r"^# (tests/\S+\.py::\S+)$", user, re.MULTILINE)
        if failing:
            toolbox.call("run_tests", {"selection": " ".join(failing[:2])})
        else:
            toolbox.call("list_routes", {})
        return text, usage

    # -- entry point ---------------------------------------------------------------------

    def complete(self, role, system, user, effort=None, stream=None):
        self.systems.append((role, system))
        text, usage = self._reply(role, system, user)
        if stream:  # like a real model: the reply arrives in pieces (FAKE_LLM_DELAY slows it down for demos)
            delay = float(os.environ.get("FAKE_LLM_DELAY", "0"))
            for i in range(0, len(text), 120):
                stream(text[i:i + 120])
                time.sleep(delay)
        return text, usage

    def _reply(self, role, system, user):
        usage = Usage(prompt_tokens=100, completion_tokens=50, cost_usd=0.0001, calls=1, models={role: 1})
        if "You fix Python syntax errors" in system:
            self.calls.append("syntax_fix")
            path = re.search(r"^(\S+) does not compile", user).group(1)
            code = _section(user, "```python\n", "```").replace(*reversed(MISTAKES["syntax"]))
            return f"### FILE: {path}\n```python\n{code}```", usage
        if "Write tests/test_rules.py" in system:
            self.calls.append("rule_tests")
            return self._rule_tests(user), usage
        if "Architect of VibeStack" in system:
            self.calls.append("spec")
            return self._architect(user), usage
        if "web app's look" in system:
            self.calls.append("ui")
            return user.split("Default (improve on it):\n", 1)[1], usage
        if "Code Generator" in system:
            self.calls.append("entity")
            return self._service(system, user), usage
        if "Reflector" in system:
            self.calls.append(f"reflect:{role}")
            return self._reflect(user), usage
        if "Judge" in system:
            self.calls.append("judge")
            return "VERDICT: CODE_WRONG: the requirements support the test", usage
        raise AssertionError(f"unexpected prompt: {system[:60]}")
