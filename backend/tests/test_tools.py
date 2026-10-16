"""The Reflector's tools: built-in tools, plugin tools, MCP tools and the function-calling loop."""

import threading

from langchain_core.messages import AIMessage
from mcp.server.mcpserver import MCPServer

from agent import plugins
from agent.codegen import render_project
from agent.fake_llm import FakeLLM
from agent.graph import initial_state
from agent.llm import ProviderLLM
from agent.mcp_tools import load_tools
from agent.state import ProjectSpec
from agent.tools import Toolbox, tool_from_function
from tests.specs import SHOP
from tests.test_graph import _run


def _box(**kw):
    spec = ProjectSpec.model_validate(SHOP)
    return Toolbox(render_project(spec), spec, **kw), spec


def test_function_becomes_a_tool_description():
    def lookup(name: str, limit: int = 3) -> str:
        """Find a thing by name.

        Longer notes are not sent to the model."""
        return name

    tool = tool_from_function(lookup)
    assert tool.schema()["function"] == {
        "name": "lookup",
        "description": "Find a thing by name.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "limit": {"type": "integer"}},
                       "required": ["name"]},
    }


def test_builtin_tools_read_search_and_protect_tests():
    box, _ = _box(max_calls=20)
    assert "app/main.py" in box.call("list_files", {})
    assert "no such file" in box.call("read_file", {"path": "nope.py"})
    assert "app/models/order.py" in box.call("search_code", {"pattern": "class Order"})
    routes = box.call("list_routes", {})
    assert "POST /orders/{item_id}/checkout" in routes and "GET /health" in routes
    assert box.call("write_file", {"path": "tests/test_smoke.py", "content": "x"}).startswith("refused")
    assert box.call("write_file", {"path": "app/new.py", "content": "x"}).startswith("refused")
    assert box.call("run_tests", {"selection": "-p evil"}).startswith("selection must")
    assert box.call("check_frontend", {}) == "frontend matches the backend"
    assert box.changes() == {}


def test_staged_fix_is_tested_and_reported():
    box, _ = _box()
    main = box.files["app/main.py"]
    box.call("write_file", {"path": "app/main.py", "content": main.replace("import", "imprt", 1)})
    result = box.call("run_tests", {"selection": "tests/test_smoke.py::test_health"})
    assert result.startswith("FAILED")
    assert list(box.changes()) == ["app/main.py"]
    assert box.calls[-1] == {"tool": "run_tests", "args": {"selection": "tests/test_smoke.py::test_health"},
                             "result": "FAILED (no tests ran)"}  # the app no longer imports


def test_tool_budget_and_bad_calls():
    box, _ = _box(max_calls=2)
    assert box.call("read_file", {}).startswith("error: TypeError")
    assert box.call("nope", {}) == "unknown tool"
    assert box.call("list_files", {}).startswith("tool budget used up")
    assert [c["tool"] for c in box.calls] == ["read_file", "nope"]


def test_plugin_tools_hook():
    chosen = plugins.chosen(["pypi_lookup"])
    tools = plugins.tools(chosen)
    assert [t.name for t in tools] == ["pypi_package"]
    assert "tools" in next(p["hooks"] for p in plugins.describe() if p["name"] == "pypi_lookup")


def test_mcp_server_tools_are_wrapped_and_callable():
    server = MCPServer(name="docs")

    @server.tool()
    def lookup(topic: str) -> str:
        """Look up documentation for a topic."""
        return f"docs for {topic}"

    tools = load_tools([{"name": "docs", "server": server}, {"name": "down", "url": "http://127.0.0.1:9/mcp"}])
    assert [t.name for t in tools] == ["docs__lookup"]  # the unreachable server is skipped
    box, _ = _box(extra=tools)
    assert box.call("docs__lookup", {"topic": "sqlalchemy"}) == "docs for sqlalchemy"


class _ScriptedChat:
    """Stands in for a LangChain chat model: replies from a script, records what it was sent."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.tool_choices = []

    def bind_tools(self, schemas, tool_choice=None):
        chat = self

        class Bound:
            def invoke(self, messages):
                chat.tool_choices.append(tool_choice)
                return chat.replies.pop(0)

        return Bound()


def test_function_calling_loop_runs_tools_then_answers():
    box, _ = _box(max_calls=2)
    chat = _ScriptedChat([
        AIMessage("", tool_calls=[{"name": "list_routes", "args": {}, "id": "1"}]),
        AIMessage("", tool_calls=[{"name": "read_file", "args": {"path": "app/main.py"}, "id": "2"}]),
        AIMessage("DIAGNOSIS: done"),
    ])
    llm = ProviderLLM.__new__(ProviderLLM)
    llm._models = {"cheap": ("m", chat)}
    llm._slots = threading.BoundedSemaphore(1)
    text, usage = llm.complete_with_tools("cheap", "system", "user", box)
    assert text == "DIAGNOSIS: done"
    assert [c["tool"] for c in box.calls] == ["list_routes", "read_file"]
    assert chat.tool_choices == [None, None, "none"]  # the budget is spent: the last round must answer
    assert usage.calls == 3


def test_reflector_uses_tools_to_check_its_fix():
    fake = FakeLLM(mistake="swallow_fk")
    values = _run(initial_state("blog", {"codegen_mode": "llm"}), fake)
    assert values["status"] == "succeeded"
    reflector = next(e for e in values["ledger"] if e["agent"].startswith("reflector"))
    assert [c["tool"] for c in reflector["tools"]][:2] == ["read_file", "write_file"]
    # The first fix is checked before answering; it repairs one of the two failing tests.
    run = next(c for c in reflector["tools"] if c["tool"] == "run_tests")
    assert run["result"] == "FAILED (1 failed, 1 passed)"
    last = [e for e in values["ledger"] if e["agent"].startswith("reflector")][-1]
    assert last["tools"][-1]["result"] == "PASSED (1 passed)"


def test_tools_can_be_switched_off_for_the_ablation_study():
    fake = FakeLLM(mistake="typo")
    values = _run(initial_state("blog", {"codegen_mode": "llm", "tools": False}), fake)
    assert values["status"] == "succeeded"
    reflector = next(e for e in values["ledger"] if e["agent"].startswith("reflector"))
    assert "tools" not in reflector
