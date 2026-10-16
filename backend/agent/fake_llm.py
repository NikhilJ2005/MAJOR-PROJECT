"""Deterministic stand-in for OpenRouter that plays every agent role.

Used by the test-suite, and by `FAKE_LLM=1` so the UI can be developed and
demoed without an API key or token spend. It never invents application code:
entity files echo the reference templates. With `mistake=...` the "API engineer"
makes a typical model slip, so tests can watch the agents find and repair a
bug that nobody planted in the pipeline:

    "typo"        a misspelled import (caught when the app is imported)
    "swallow_fk"  constraint errors are not turned into 409 (only the spec-derived
                  "unknown foreign key" edge-case test notices: a 500 instead of 409)
"""

from __future__ import annotations

import json
import re

from .llm import Usage

BLOG_SPEC = {
    "project_name": "blog_api",
    "description": "A blog with posts and comments",
    "auth": True,
    "features": ["search"],
    "entities": [
        {"name": "Post", "fields": [{"name": "title"}, {"name": "body", "type": "text"}, {"name": "author_id", "references": "User"}]},
        {"name": "Comment", "fields": [{"name": "content", "type": "text"}, {"name": "post_id", "references": "Post"}]},
    ],
}

MISTAKES = {
    "typo": ("from app.errors import ApiError", "from app.errors import ApiErorr"),
    "swallow_fk": ("except IntegrityError as exc:", "except ValueError as exc:"),
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

    def _entity(self, user: str) -> str:
        reference = user.split("Reference implementation:\n", 1)[1]
        repeat = self.mistake_name == "swallow_fk"  # a typo happens once; a misunderstanding happens everywhere
        if self.mistake and self.mistake[0] in reference and (repeat or not self._made_typo):
            self._made_typo = True
            reference = reference.replace(*self.mistake, -1 if repeat else 1)
        return "RATIONALE: kept the reference contract\n" + reference

    def _first_entity(self, user: str) -> tuple[dict, dict]:
        from .codegen import smoke_cases
        from .state import ProjectSpec

        design = json.loads(_section(user, "Design (JSON):\n", "\n\nCode:"))
        spec = ProjectSpec.model_validate(design)
        cases = smoke_cases(spec)
        with_fk = [c for c, e in zip(cases, spec.entities) if e.foreign_keys]
        return (with_fk or cases)[0], design

    def _rule_tests(self, user: str) -> str:
        case, _ = self._first_entity(user)
        return (
            "### FILE: tests/test_rules.py\n```python\n"
            "def test_list_respects_limit(client, user_headers):\n"
            f"    r = client.get('/{case['table']}/', params={{'limit': 1}}, headers=user_headers)\n"
            "    assert r.status_code == 200, r.text\n"
            "    assert len(r.json()) <= 1\n```\n"
        )

    def _reflect(self, user: str) -> str:
        m = re.search(r"File most likely at fault: (\S+)\n```python\n(.*?)```", user, re.DOTALL)
        path, content = m.group(1), m.group(2)
        if self.fix_reflection:
            for good, bad in MISTAKES.values():
                content = content.replace(bad, good)
        else:
            content += "\n# attempted fix (still broken)\n"
        return f"DIAGNOSIS: the code does not match the expected behaviour\n### FILE: {path}\n```python\n{content}```"

    # -- entry point ---------------------------------------------------------------------

    def complete(self, role, system, user):
        usage = Usage(prompt_tokens=100, completion_tokens=50, cost_usd=0.0001, calls=1, models={role: 1})
        if "Write tests/test_rules.py" in system:
            self.calls.append("rule_tests")
            return self._rule_tests(user), usage
        if "Architect of VibeStack" in system:
            self.calls.append("spec")
            return self._architect(user), usage
        if "Code Generator" in system:
            self.calls.append("entity")
            return self._entity(user), usage
        if "Reflector" in system:
            self.calls.append(f"reflect:{role}")
            return self._reflect(user), usage
        if "Judge" in system:
            self.calls.append("judge")
            return "VERDICT: CODE_WRONG: the requirements support the test", usage
        raise AssertionError(f"unexpected prompt: {system[:60]}")
