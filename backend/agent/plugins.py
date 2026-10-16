"""Plugins: extend VibeStack without touching its code.

A plugin is one .py file in backend/plugins/ (or the folder in PLUGINS_DIR); its file
name is its name. It may define any of these:

    DESCRIPTION = "one line shown in the UI"
    def extra_files(spec) -> dict[str, str]      add files to every generated project
    def extra_checks(files) -> list[Finding]      more static checks (Finding is in agent/bughunt.py)
    def prompt_notes(stack) -> str                extra instructions for the AI code writers
    def tools() -> list[callable]                 extra tools the Reflector may call (agent/tools.py)

A run uses the plugins listed in options["plugins"] (toggled in the UI); the PLUGINS
environment variable (comma-separated) sets the default. A broken plugin is skipped and
logged; it never breaks a run.
"""

from __future__ import annotations

import importlib.util
import logging
import os
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
         "default": name in DEFAULT_PLUGINS}
        for name, m in available().items()
    ]


def chosen(names: list[str] | None) -> list[tuple[str, ModuleType]]:
    """The plugins a run asked for (or the defaults), in a stable order."""
    loaded = available()
    wanted = DEFAULT_PLUGINS if names is None else names
    return [(n, loaded[n]) for n in wanted if n in loaded]


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
