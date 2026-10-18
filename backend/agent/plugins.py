"""Plugins: extend VibeStack without touching its code.

A plugin is one .py file in backend/plugins/ (or the folder in PLUGINS_DIR); its file
name is its name. It may define any of these:

    DESCRIPTION = "one line shown in the UI"
    WHEN = "when it helps"                        read by the Architect, which picks plugins per app
    KEYWORDS = ("k8s", ...)                       words in the request that turn it on
    ALWAYS = True                                 on for every build (safety checks)
    def wanted(spec) -> str | None                a reason to turn it on for this design, or None
    def extra_files(spec) -> dict[str, str]      add files to every generated project
    def extra_checks(files) -> list[Finding]      more static checks (Finding is in agent/bughunt.py)
    def prompt_notes(stack) -> str                extra instructions for the AI code writers
    def tools() -> list[callable]                 extra tools the Reflector may call (agent/tools.py)

The agents choose the plugins themselves (resolve): the Architect names the ones the app needs,
with a reason, and the Planner adds the ALWAYS ones, those whose KEYWORDS appear in the request and
those whose wanted(spec) says so. The user can still force plugins on (options["plugins"]) or off
(options["plugins_off"]); options["auto_plugins"] = False turns the agents' choice off. The PLUGINS
environment variable (comma-separated) adds defaults. A broken plugin is skipped and logged; it never
breaks a run.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import re
from pathlib import Path
from types import ModuleType
from typing import Any

from .state import ProjectSpec

log = logging.getLogger(__name__)

PLUGINS_DIR = Path(os.environ.get("PLUGINS_DIR", Path(__file__).resolve().parents[1] / "plugins"))
DEFAULT_PLUGINS = [p.strip() for p in os.environ.get("PLUGINS", "").split(",") if p.strip()]


def available() -> dict[str, ModuleType]:
    """Load every plugin file in the plugins folder."""
    found: dict[str, ModuleType] = {}
    for path in sorted(PLUGINS_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"vibestack_plugin_{path.stem}", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            found[path.stem] = module
        except Exception:  # a broken plugin is skipped, never fatal
            log.exception("could not load plugin %s", path.name)
    return found


def describe() -> list[dict[str, Any]]:
    """Name, description and hooks of every plugin, for /api/config and the UI."""
    hooks = ("extra_files", "extra_checks", "prompt_notes", "tools")
    return [
        {"name": name, "description": getattr(m, "DESCRIPTION", ""), "hooks": [h for h in hooks if hasattr(m, h)],
         "default": name in DEFAULT_PLUGINS, "when": getattr(m, "WHEN", ""), "always": bool(getattr(m, "ALWAYS", False))}
        for name, m in available().items()
    ]


def chosen(names: list[str] | None) -> list[tuple[str, ModuleType]]:
    """The plugins a run asked for (or the defaults), in a stable order."""
    loaded = available()
    wanted = DEFAULT_PLUGINS if names is None else names
    return [(n, loaded[n]) for n in wanted if n in loaded]


def catalog() -> str:
    """The plugin list the Architect reads when it decides which plugins an app needs."""
    lines = [f"- {name}: {getattr(m, 'DESCRIPTION', '')}. Use when {getattr(m, 'WHEN', 'it clearly helps')}."
             for name, m in available().items() if not getattr(m, "ALWAYS", False)]
    return "\n".join(lines)


def resolve(options: dict[str, Any], architect: list[dict[str, str]], spec: ProjectSpec, prompt: str) -> list[dict[str, str]]:
    """The plugins a build uses, each with who chose it and why: [{name, by, reason}], in a stable order.

    by: "user" (forced on), "architect" (the design asked for it), "planner" (always on, a keyword in the
    request, or the plugin's own wanted(spec) rule), "default" (PLUGINS environment variable).
    """
    loaded = available()
    off = set(options.get("plugins_off") or [])
    picked: dict[str, dict[str, str]] = {}

    def add(name: str, by: str, reason: str) -> None:
        if name in loaded and name not in off and name not in picked:
            picked[name] = {"name": name, "by": by, "reason": reason}

    for name in options.get("plugins") or []:
        add(name, "user", "turned on by you")
    if options.get("auto_plugins", True):
        for choice in architect:
            add(choice.get("name", ""), "architect", choice.get("reason") or "the design needs it")
        text = prompt.lower()
        for name, m in loaded.items():
            if getattr(m, "ALWAYS", False):
                add(name, "planner", "on for every build")
            word = next((k for k in getattr(m, "KEYWORDS", ()) if re.search(rf"\b{re.escape(k)}", text)), None)
            if word:
                add(name, "planner", f'the request mentions "{word}"')
            reason = _call(name, m, "wanted", spec, default=None)
            if reason:
                add(name, "planner", reason)
    for name in DEFAULT_PLUGINS:
        add(name, "default", "on by default (PLUGINS setting)")
    return [picked[n] for n in loaded if n in picked]


def _call(name: str, module: ModuleType, hook: str, *args: Any, default: Any) -> Any:
    fn = getattr(module, hook, None)
    if fn is None:
        return default
    try:
        return fn(*args)
    except Exception:  # a broken plugin is skipped, never fatal
        log.exception("plugin %s failed in %s", name, hook)
        return default


def extra_files(plugins: list[tuple[str, ModuleType]], spec: ProjectSpec) -> dict[str, dict[str, str]]:
    """{plugin name: {path: content}} from every plugin's extra_files hook."""
    return {name: _call(name, m, "extra_files", spec, default={}) or {} for name, m in plugins}


def extra_checks(plugins: list[tuple[str, ModuleType]], files: dict[str, str]) -> list[Any]:
    return [f for name, m in plugins for f in (_call(name, m, "extra_checks", files, default=[]) or [])]


def prompt_notes(plugins: list[tuple[str, ModuleType]], stack: str) -> str:
    notes = [_call(name, m, "prompt_notes", stack, default="") for name, m in plugins]
    return "\n".join(f"- {n.strip()}" for n in notes if n and n.strip())


def tools(plugins: list[tuple[str, ModuleType]]) -> list[Any]:
    """Every plugin's tools, described for the model (name and description from the function)."""
    from .tools import tool_from_function

    found = []
    for name, m in plugins:
        for fn in _call(name, m, "tools", default=[]) or []:
            try:
                found.append(tool_from_function(fn))
            except Exception:  # a broken plugin is skipped, never fatal
                log.exception("plugin %s: tool %r cannot be described", name, fn)
    return found
