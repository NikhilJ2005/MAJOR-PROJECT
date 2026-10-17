"""The generated frontend: a complete React app that talks to the generated backend.

The app itself (templates/frontend/app.js and index.html) is fixed, tested code. What changes per
project is data:

    app/static/config.json  - generated from the design: entities, fields, rules, actions, live updates
    app/static/ui.json      - written by the Code Generator: name, tagline, colours, labels, dashboard

`check_frontend` is the Validator's full-stack contract check: everything the frontend refers to
must exist in the backend's design. A mismatch fails validation and is healed like any other bug.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from .codegen import search_field
from .state import EntitySpec, ProjectSpec, pluralize

CONFIG = "app/static/config.json"
UI = "app/static/ui.json"

Accent = Literal["indigo", "violet", "emerald", "sky", "rose", "amber", "teal", "slate"]


class EntityUI(BaseModel):
    label: str = Field(description="Plural name shown in the menu, e.g. 'Orders'")
    icon: str = Field(default="•", description="One emoji")
    description: str = ""


class Card(BaseModel):
    title: str
    entity: str = Field(description="Entity name, e.g. Order")
    group_by: str | None = Field(default=None, description="Optional: a field with choices to count by, e.g. status")


class UIConfig(BaseModel):
    """How the generated app looks: the only frontend part the AI writes."""

    app_name: str
    tagline: str = ""
    accent: Accent = "indigo"
    entities: dict[str, EntityUI] = Field(default_factory=dict, description="Keyed by entity name")
    dashboard: list[Card] = Field(default_factory=list, description="2-6 summary cards")


_ICONS = {"user": "👤", "customer": "👤", "member": "👤", "patient": "🧑", "doctor": "🩺", "order": "🧾",
          "item": "📦", "product": "📦", "category": "🏷️", "task": "✅", "issue": "🐞", "project": "📁",
          "log": "📜", "agent": "🤖", "graph": "🕸️", "workflow": "🔀", "post": "📝", "comment": "💬",
          "book": "📚", "loan": "🔁", "event": "📅", "booking": "🎟️", "ticket": "🎟️", "venue": "🏟️",
          "appointment": "📅", "invoice": "🧾", "payment": "💳", "review": "⭐", "message": "✉️"}


def _icon(e: EntitySpec) -> str:
    """A fitting emoji from words in the entity name (the Code Generator may choose better ones)."""
    words = [w.lower() for w in re.findall(r"[A-Z][a-z]*", e.name)]
    return next((_ICONS[w] for w in reversed(words) if w in _ICONS), "▪️")


def _label(e: EntitySpec) -> str:
    words = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", e.name)
    head, _, last = words.rpartition(" ")
    plural = pluralize(last.lower())
    return f"{head} {last[0]}{plural[1:]}".strip()


def frontend_config(spec: ProjectSpec) -> dict[str, Any]:
    """Everything the frontend needs to know about the backend, derived from the design."""
    entities = []
    for e in spec.entities:
        entities.append({
            "name": e.name,
            "table": e.table,
            "access": e.write_access,
            "roles": e.write_roles,
            "realtime": e.realtime,
            "search": search_field(e),
            "fields": [f.model_dump(include={"name", "type", "required", "unique", "references", "choices",
                                              "min", "max", "transitions"}) for f in e.fields],
            "actions": [a.model_dump(include={"name", "method", "path", "description", "admin_only", "roles"})
                        for a in spec.actions_for(e)],
        })
    return {"project": spec.project_name, "description": spec.description, "auth": spec.auth, "entities": entities,
            "roles": [r.model_dump(include={"name", "self_signup"}) for r in spec.roles], "features": spec.features}


def default_ui(spec: ProjectSpec) -> dict[str, Any]:
    """A sensible look without any AI: used by template mode and as the repair fallback."""
    cards = [{"title": f"Total {_label(e).lower()}", "entity": e.name} for e in spec.entities[:3]]
    for e in spec.entities:
        status = next((f for f in e.fields if f.choices), None)
        if status:
            cards.append({"title": f"{_label(e)} by {status.name}", "entity": e.name, "group_by": status.name})
            break
    ui = UIConfig(
        app_name=spec.project_name.replace("_", " ").title(),
        tagline=spec.description,
        entities={e.name: EntityUI(label=_label(e), icon=_icon(e)) for e in spec.entities},
        dashboard=cards,
    )
    return ui.model_dump()


def frontend_files(spec: ProjectSpec) -> dict[str, str]:
    return {
        CONFIG: json.dumps(frontend_config(spec), indent=1) + "\n",
        UI: json.dumps(default_ui(spec), indent=1) + "\n",
    }


def check_frontend(files: dict[str, str], spec: ProjectSpec) -> list[str]:
    """Problems that would break the generated frontend (empty list = frontend and backend agree)."""
    if UI not in files:
        return []  # projects from before the frontend existed
    problems: list[str] = []
    if files.get(CONFIG, "").strip() != json.dumps(frontend_config(spec), indent=1):
        problems.append(f"{CONFIG}:1: FrontendContractError config.json does not match the backend design")
    try:
        ui = UIConfig.model_validate(json.loads(files[UI]))
    except (ValueError, ValidationError) as exc:
        first = str(exc).splitlines()[0][:200]
        return problems + [f"{UI}:1: FrontendContractError ui.json is not valid: {first}"]
    entities = {e.name: e for e in spec.entities}
    for name in ui.entities:
        if name not in entities:
            problems.append(f"{UI}:1: FrontendContractError entities.{name} is not an entity of the backend")
    for card in ui.dashboard:
        entity = entities.get(card.entity)
        if entity is None:
            problems.append(f"{UI}:1: FrontendContractError dashboard card '{card.title}' uses unknown entity {card.entity}")
            continue
        if card.group_by:
            field = next((f for f in entity.fields if f.name == card.group_by), None)
            if field is None or not field.choices:
                problems.append(f"{UI}:1: FrontendContractError dashboard card '{card.title}' groups by "
                                f"{card.entity}.{card.group_by}, which is not a field with choices")
    return problems
