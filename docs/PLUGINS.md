# Writing a VibeStack plugin

A plugin is **one Python file** in `backend/plugins/`; its file name is its name. VibeStack loads it with
`importlib`, shows it in the UI as a toggle, and calls the hooks it defines. A plugin that crashes is logged
and skipped; it never breaks a run.

| Hook | When it runs | Use it for |
|---|---|---|
| `extra_files(spec) -> dict[str, str]` | while planning | add files to every generated project (deploy manifests, API collections, docs) |
| `extra_checks(files) -> list[Finding]` | at every validation | your own static checks; `"high"` findings fail validation and are repaired, `"low"` ones become review notes |
| `prompt_notes(stack) -> str` | when AI agents write or repair code | team coding rules the AI must follow |
| `tools() -> list[function]` | when the Reflector repairs code | extra tools the model may call; each is a plain function with type hints and a docstring |

`spec` is the project design (`agent/state.py: ProjectSpec`): `spec.entities`, `spec.actions`, `spec.auth`
and `spec.stack`. `files` maps each path to its content.

## Example: an extra check in 15 lines

```python
# backend/plugins/no_float_money.py
from agent.bughunt import Finding

DESCRIPTION = "Money fields must use decimal, not float"


def extra_checks(files):
    found = []
    for path, source in files.items():
        if path.startswith("app/models/"):
            for number, line in enumerate(source.splitlines(), 1):
                if "price" in line and "Float" in line:
                    found.append(Finding(path, number, "float-money", "high", "use decimal for prices"))
    return found
```

Because the finding is `"high"`, validation fails with
`app/models/product.py:12: StaticCheckError [float-money] use decimal for prices`. The Error Classifier
labels it `static` and the Reflector fixes it, just like any other bug.

## Example: a tool for the Reflector

```python
# backend/plugins/pypi_lookup.py (included)
def pypi_package(name: str) -> str:
    """Look up a Python package on PyPI: its latest version and a one-line summary."""
    ...

def tools():
    return [pypi_package]
```

The function's name, parameters (`str`, `int`, `float` and `bool`) and the first paragraph of its docstring
become the tool description the model sees (`agent/tools.py: tool_from_function`). Whatever it returns is
sent back to the model as text, and each call is recorded in the ledger.

## Turning plugins on

- Per run: toggle them in the UI, or send `"plugins": ["postman", "kubernetes"]` to `POST /api/projects`.
- By default: `PLUGINS=postman,kubernetes` in the environment.
- From another folder: `PLUGINS_DIR=/path/to/plugins`.

## Included examples

| Plugin | Hook | What it does |
|---|---|---|
| `postman` | `extra_files` | `postman_collection.json` with every endpoint and action |
| `kubernetes` | `extra_files` | `k8s/deployment.yaml` (probes on `/ready` and `/health`, resource limits) and `k8s/service.yaml` |
| `todo_check` | `extra_checks` | review note for each `TODO`/`FIXME` left in the code |
| `strict_inputs` | `prompt_notes` | asks the AI to trim text and reject blank strings |
| `pypi_lookup` | `tools` | the Reflector can check a package's latest version and summary on PyPI |
| `deploy_targets` | `extra_files` | `render.yaml`, `fly.toml` and `railway.json`: the generated app deploys to Render, Fly.io or Railway |
| `seed_data` | `extra_files` | `scripts/seed.py`: fills a running app with sample rows through its own API (links, choices and value ranges respected) |
| `openapi_export` | `extra_files` | `openapi.json` (OpenAPI 3.1) built from the design, for Flask and Django too |
| `secrets_scan` | `extra_checks` | fails validation when a real-looking key or token appears in any file (YAML, JSON, `.env`, Markdown …) |
