"""Deterministic stand-in for OpenRouter that plays every agent role.

Used by the test-suite, and by `FAKE_LLM=1` so the UI can be developed and
demoed without an API key or token spend. It never invents code: entity files
echo the reference templates. With `buggy=True` the "API engineer" makes a
typical model mistake (a misspelled import), so tests can watch the agents
find and repair a bug that nobody planted on purpose.
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


class FakeLLM:
    """Plays architect, API engineer and reflector."""

    def __init__(self, spec=None, fix_reflection=True, buggy=False):
        self.spec = spec or BLOG_SPEC
        self.fix_reflection = fix_reflection
        self.buggy = buggy
        self._made_mistake = False
        self.calls: list[str] = []

    def complete(self, role, system, user):
        usage = Usage(prompt_tokens=100, completion_tokens=50, cost_usd=0.0001, calls=1, models={role: 1})
        if "Architect of VibeStack" in system:
            self.calls.append("spec")
            return json.dumps(self.spec), usage
        if "API Engineer" in system:
            self.calls.append("entity")
            reference = user.split("Reference implementation:\n", 1)[1]
            if self.buggy and not self._made_mistake:
                self._made_mistake = True  # one slip, like a real model typo
                reference = reference.replace("import Session", "import Sesion", 1)
            return "RATIONALE: kept the reference contract\n" + reference, usage
        if "Reflector" in system:
            self.calls.append(f"reflect:{role}")
            m = re.search(r"File most likely at fault: (\S+)\n```python\n(.*?)```", user, re.S)
            path, content = m.group(1), m.group(2)
            if self.fix_reflection:
                content = content.replace("Sesion", "Session")
            else:
                content += "\n# attempted fix (still broken)\n"
            return f"DIAGNOSIS: misspelled import\n### FILE: {path}\n```python\n{content}```", usage
        raise AssertionError(f"unexpected prompt: {system[:60]}")


