"""Deterministic stand-in for OpenRouter that plays every agent role.

Used by the test-suite, and by `FAKE_LLM=1` so the UI can be developed and
demoed without an API key or token spend. It never invents code: entity files
echo the reference templates and the reflector fixes the demo fault.
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
    """Plays spec architect, API engineer, reviewer and reflector."""

    def __init__(self, spec=None, fix_reflection=True):
        self.spec = spec or BLOG_SPEC
        self.fix_reflection = fix_reflection
        self.calls: list[str] = []

    def complete(self, role, system, user):
        usage = Usage(prompt_tokens=100, completion_tokens=50, cost_usd=0.0001, calls=1, models={role: 1})
        if "Spec Architect" in system:
            self.calls.append("spec")
            return json.dumps(self.spec), usage
        if "API Engineer" in system:
            self.calls.append("entity")
            reference = user.split("Reference implementation:\n", 1)[1]
            return "RATIONALE: kept the reference contract\n" + reference, usage
        if "Reviewer" in system:
            self.calls.append("review")
            return json.dumps({"findings": [{"severity": "Low", "file": "app/main.py", "issue": "no rate limiting"}]}), usage
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


