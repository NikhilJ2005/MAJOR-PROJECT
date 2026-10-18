"""VibeStack's own MCP tool server, started inside the container over stdio (MCP_PRESETS=builtin).

It gives the agents developer tools that need no outside service and no AI client:

- python_docs: the documentation of the exact library versions the generated apps run on
  (SQLAlchemy, Django, Flask, FastAPI, Pydantic ...), so a repair never relies on a remembered API
- package_versions: which versions those are
- check_manifests: a structural check of Kubernetes YAML (kinds, names, containers, probes)
- pypi_package: the latest release of a package on PyPI

Run by hand: python -m agent.mcp_builtin (it speaks MCP on stdin/stdout).
"""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import pydoc
import urllib.request

LIBRARIES = ["fastapi", "starlette", "sqlalchemy", "alembic", "flask", "werkzeug", "django", "pydantic", "pyjwt",
             "psycopg", "redis", "httpx", "pytest"]
MAX_CHARS = 6000


def python_docs(name: str) -> str:
    """Documentation of a Python module, class or function as installed here, e.g. sqlalchemy.orm.Session.get."""
    target = None
    parts = name.strip().split(".")
    for i in range(len(parts), 0, -1):  # import the longest importable prefix, then walk the attributes
        try:
            target = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        for attr in parts[i:]:
            target = getattr(target, attr, None)
            if target is None:
                return f"{name}: no such attribute"
        break
    if target is None:
        return f"{name}: not installed"
    text = pydoc.render_doc(target, renderer=pydoc.plaintext)
    return text[:MAX_CHARS] + ("\n... (cut)" if len(text) > MAX_CHARS else "")


def package_versions() -> str:
    """The installed versions of the libraries generated apps use."""
    lines = []
    for lib in LIBRARIES:
        try:
            lines.append(f"{lib}=={importlib.metadata.version(lib)}")
        except importlib.metadata.PackageNotFoundError:
            lines.append(f"{lib}: not installed")
    return "\n".join(lines)


def check_manifests(yaml_text: str) -> str:
    """Check Kubernetes YAML: every object has apiVersion, kind and a name; workloads have containers,
    images and probes. Returns the problems found, or 'ok'."""
    import yaml

    try:
        objects = [o for o in yaml.safe_load_all(yaml_text) if o]
    except yaml.YAMLError as exc:
        return f"not valid YAML: {exc}"
    problems = []
    for i, o in enumerate(objects, 1):
        where = f"object {i} ({o.get('kind', '?')} {o.get('metadata', {}).get('name', '?')})"
        for key in ("apiVersion", "kind"):
            if not o.get(key):
                problems.append(f"{where}: missing {key}")
        if not (o.get("metadata") or {}).get("name"):
            problems.append(f"{where}: missing metadata.name")
        if o.get("kind") in ("Deployment", "StatefulSet", "Job"):
            pod = ((o.get("spec") or {}).get("template") or {}).get("spec") or {}
            containers = pod.get("containers") or []
            if not containers:
                problems.append(f"{where}: no containers")
            for c in containers:
                if not c.get("image"):
                    problems.append(f"{where}: container {c.get('name')} has no image")
                if o["kind"] == "Deployment" and not c.get("readinessProbe"):
                    problems.append(f"{where}: container {c.get('name')} has no readinessProbe")
    return "\n".join(problems) or f"ok ({len(objects)} objects)"


def pypi_package(name: str) -> str:
    """The latest version and summary of a package on PyPI."""
    try:
        with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=10) as r:
            info = json.load(r)["info"]
    except Exception as exc:  # noqa: BLE001 - reported to the model as text
        return f"{name}: lookup failed ({exc})"
    return f"{name} {info['version']}: {info.get('summary') or ''}"


def build_server():
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(name="vibestack-builtin", version="1.0.0")
    for fn in (python_docs, package_versions, check_manifests, pypi_package):
        server.tool()(fn)
    return server


if __name__ == "__main__":
    build_server().run()  # stdio
