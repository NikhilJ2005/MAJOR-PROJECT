"""Example plugin (tools): lets the Reflector look up a package on PyPI while it repairs code.

Useful when an error mentions a missing or renamed import: the model can check which
version exists and what the package is for, instead of guessing.
"""

import json
import urllib.error
import urllib.request

DESCRIPTION = "Gives the Reflector a PyPI lookup tool (latest version and summary of a package)"


def pypi_package(name: str) -> str:
    """Look up a Python package on PyPI: its latest version and a one-line summary."""
    url = f"https://pypi.org/pypi/{urllib.request.quote(name)}/json"
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            info = json.load(response)["info"]
    except urllib.error.HTTPError as exc:
        return f"{name}: not found on PyPI ({exc.code})"
    except OSError as exc:
        return f"PyPI unreachable: {exc}"
    return f"{info['name']} {info['version']}: {info.get('summary') or 'no summary'}"


def tools() -> list:
    return [pypi_package]
