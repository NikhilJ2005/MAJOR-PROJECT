"""Model-agnostic LLM client on top of OpenRouter.

Responsibilities:
- tiered routing: callers ask for a *role* ("strong" or "cheap"), not a model
- fallback: if the routed model errors, retry once on the fallback model
- structured output: JSON extracted from the reply and validated by Pydantic,
  with one repair round-trip that feeds the validation error back to the model
- usage accounting: tokens + cost (OpenRouter-reported when available)
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .config import settings

log = logging.getLogger(__name__)

Role = Literal["strong", "cheap"]
T = TypeVar("T", bound=BaseModel)

# Rough USD per 1M tokens (input, output), only used when OpenRouter does not report cost.
_PRICE_TABLE: dict[str, tuple[float, float]] = {
    "anthropic/claude-sonnet-4.6": (3.0, 15.0),
    "qwen/qwen3-coder": (0.22, 0.95),
    "deepseek/deepseek-chat-v3.1": (0.20, 0.80),
    "openai/gpt-4o-mini": (0.15, 0.60),
    # Estimates; free tiers cost nothing within their limits.
    "openai/gpt-oss-120b": (0.15, 0.60),
    "openai/gpt-oss-20b": (0.075, 0.30),
    "gemini-4-argon": (2.00, 10.00),  # launch price until 30 Nov 2026, then 4 / 20
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    models: dict[str, int] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_state(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "llm_calls": self.calls,
        }

    def add(self, other: Usage) -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cost_usd += other.cost_usd
        self.calls += other.calls
        for m, n in other.models.items():
            self.models[m] = self.models.get(m, 0) + n


class LLM(Protocol):
    def complete(self, role: Role, system: str, user: str) -> tuple[str, Usage]: ...


def extract_json(text: str) -> Any:
    """Pull the first JSON object out of a model reply (tolerates ``` fences and chatter)."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found in model reply")
        candidate = text[start : end + 1]
    return json.loads(candidate)


def structured(llm: LLM, role: Role, system: str, user: str, model: type[T]) -> tuple[T, Usage]:
    schema = json.dumps(model.model_json_schema(), indent=None)
    sys = f"{system}\n\nReply with ONLY a JSON object matching this JSON schema:\n{schema}"
    total = Usage()
    prompt = user
    last_err: Exception | None = None
    for _ in range(2):
        text, usage = llm.complete(role, sys, prompt)
        total.add(usage)
        try:
            return model.model_validate(extract_json(text)), total
        except (ValueError, ValidationError) as exc:
            last_err = exc
            prompt = (
                f"{user}\n\nYour previous reply was invalid:\n{text[:4000]}\n\n"
                f"Error: {exc}\nReturn corrected JSON only."
            )
    raise LLMError(f"model did not return valid {model.__name__}: {last_err}")


def parse_file_blocks(text: str) -> dict[str, str]:
    """Parse replies of the form '### FILE: path' followed by a fenced code block."""
    files: dict[str, str] = {}
    pattern = re.compile(r"###\s*FILE:\s*(\S+)\s*\n```[a-zA-Z]*\n(.*?)```", re.DOTALL)
    for path, body in pattern.findall(text):
        files[path.strip()] = body.rstrip() + "\n"
    return files


def model_options(model: str, reasoning: str) -> dict[str, Any]:
    """ChatOpenAI settings for one model.

    - Thinking models get a thinking level (`reasoning_effort`: low, medium or high), which Gemini's
      OpenAI-compatible API maps to its thinking level.
    - Gemini 4 ignores sampling settings such as temperature, so none is sent; older models get 0.1.
    - Thinking counts against the output limit, so no low max_tokens is set.
    """
    options: dict[str, Any] = {
        "timeout": 300,
        # The OpenAI SDK retries rate limits (429) and server errors with exponential
        # backoff and honours the provider's retry-after header.
        "max_retries": settings.llm_max_retries,
    }
    thinking = settings.llm_provider in ("gemini", "openai_compatible") or model.startswith(("gemini", "openai/gpt-oss"))
    if reasoning and thinking:
        options["reasoning_effort"] = reasoning
    if not model.startswith("gemini-4"):
        options["temperature"] = 0.1
    return options


class ProviderLLM:
    """Chat models of the configured provider (OpenRouter, Groq, Gemini or any OpenAI-compatible API)."""

    def __init__(self) -> None:
        from langchain_openai import ChatOpenAI

        extra = {}
        if settings.llm_provider == "openrouter":
            extra = {"extra_body": {"usage": {"include": True}}, "default_headers": {"X-Title": "VibeStack"}}

        def make(model: str, reasoning: str) -> Any:
            return ChatOpenAI(model=model, api_key=settings.llm_api_key, base_url=settings.llm_base_url,
                              **model_options(model, reasoning), **extra)

        self._models = {
            "strong": (settings.strong_model, make(settings.strong_model, settings.strong_reasoning)),
            "cheap": (settings.cheap_model, make(settings.cheap_model, settings.cheap_reasoning)),
        }
        self._fallback = (settings.fallback_model, make(settings.fallback_model, "medium"))
        self._slots = threading.BoundedSemaphore(max(1, settings.llm_max_parallel))

    def chat(self, role: Role) -> Any:
        """The LangChain chat model for a role (used by the tool-using Reflector)."""
        return self._models[role][1]

    def complete(self, role: Role, system: str, user: str) -> tuple[str, Usage]:
        attempts = [self._models[role], self._fallback]
        last: Exception | None = None
        for name, chat in attempts:
            t0 = time.monotonic()
            try:
                with self._slots:  # at most LLM_MAX_PARALLEL calls at once
                    msg = chat.invoke([("system", system), ("user", user)])
            except Exception as exc:  # noqa: BLE001 - any provider failure triggers fallback
                log.warning("model %s failed after %.1fs: %s", name, time.monotonic() - t0, exc)
                last = exc
                continue
            return _content(msg), _usage(name, msg)
        raise LLMError(f"all models failed: {last}")

    def complete_with_tools(self, role: Role, system: str, user: str, toolbox: Any) -> tuple[str, Usage]:
        """Let the model call tools (function calling) until it answers, at most toolbox.max_calls calls.

        Each round: the model either asks for tools, we run them and send back the results, or it
        gives its final answer. When the budget is used up, the last round forbids tools.
        """
        from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

        name, chat = self._models[role]
        schemas = [t.schema() for t in toolbox.tools]
        with_tools = chat.bind_tools(schemas)
        answer_now = chat.bind_tools(schemas, tool_choice="none")
        messages: list[Any] = [SystemMessage(system), HumanMessage(user)]
        usage = Usage()
        for _ in range(toolbox.max_calls + 2):  # a safety net in case a provider ignores tool_choice
            model = answer_now if len(toolbox.calls) >= toolbox.max_calls else with_tools
            with self._slots:
                msg = model.invoke(messages)
            usage.add(_usage(name, msg))
            messages.append(msg)
            if not msg.tool_calls:
                return _content(msg), usage
            for call in msg.tool_calls:
                messages.append(ToolMessage(toolbox.call(call["name"], call["args"]), tool_call_id=call["id"]))
        return _content(msg), usage  # the last reply; the caller also keeps the staged write_file changes


OpenRouterLLM = ProviderLLM  # old name, kept for scripts


def _content(msg: Any) -> str:
    content = msg.content
    if isinstance(content, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return str(content)


def _usage(model: str, msg: Any) -> Usage:
    meta = getattr(msg, "usage_metadata", None) or {}
    pt = int(meta.get("input_tokens", 0))
    ct = int(meta.get("output_tokens", 0))
    reported = (getattr(msg, "response_metadata", {}) or {}).get("token_usage", {}) or {}
    cost = reported.get("cost")
    if cost is None:
        pin, pout = _PRICE_TABLE.get(model, (1.0, 3.0))
        cost = (pt * pin + ct * pout) / 1_000_000
    return Usage(prompt_tokens=pt, completion_tokens=ct, cost_usd=float(cost), calls=1, models={model: 1})


_llm: LLM | None = None
_overridden = False


def get_llm() -> LLM | None:
    """The process-wide client, or None when no API key is configured (offline mode)."""
    global _llm
    if _llm is None and not _overridden and settings.llm_enabled:
        if settings.fake_llm:
            from .fake_llm import FakeLLM

            _llm = FakeLLM()
        else:
            _llm = ProviderLLM()
    return _llm


def set_llm(llm: LLM | None) -> None:
    """Swap the client; None forces offline mode (used by tests and the offline eval runner)."""
    global _llm, _overridden
    _llm = llm
    _overridden = True


def check_budget(state_usage: dict[str, Any] | None) -> None:
    used = int((state_usage or {}).get("total_tokens", 0))
    if used >= settings.max_tokens_per_run:
        raise BudgetExceeded(f"token budget exhausted ({used} >= {settings.max_tokens_per_run})")
