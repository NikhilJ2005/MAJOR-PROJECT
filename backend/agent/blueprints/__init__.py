"""Built-in blueprints: verified designs of complex apps (library, escrow marketplace, clinic, inventory,
issue tracker).

Every action in them is a verified pattern (transition, adjust, move), so a blueprint builds with no
AI-written code and passes every gate the first time. They are also the Architect's worked examples: the
closest one goes into its prompt, so designs of similar apps come out complete and consistent.
"""

from __future__ import annotations

import json
import re
from functools import cache
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent


@cache
def all_blueprints() -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in sorted(HERE.glob("*.json"))]


def get(blueprint_id: str) -> dict[str, Any] | None:
    return next((b for b in all_blueprints() if b["id"] == blueprint_id), None)


def closest(prompt: str) -> dict[str, Any] | None:
    """The blueprint whose keywords the prompt mentions most (None when none fits)."""
    words = set(re.findall(r"[a-z]+", prompt.lower()))
    words |= {w.rstrip("s") for w in words}
    scored = [(sum(k in words for k in b["keywords"]), b) for b in all_blueprints()]
    score, best = max(scored, key=lambda s: s[0], default=(0, None))
    return best if score >= 1 else None


def example_text(prompt: str) -> str:
    """A worked example for the Architect: the closest blueprint's design, compact."""
    bp = closest(prompt)
    if bp is None:
        return ""
    spec = json.loads(json.dumps(bp["spec"]))
    for e in spec["entities"]:
        e["sample_rows"] = e.get("sample_rows", [])[:2]  # two rows show the format; the design matters
    return json.dumps(spec, separators=(",", ":"))


def summaries() -> list[dict[str, Any]]:
    return [{k: b[k] for k in ("id", "title", "summary", "prompt")} | {"entities": len(b["spec"]["entities"]),
            "actions": len(b["spec"].get("actions", []))} for b in all_blueprints()]
