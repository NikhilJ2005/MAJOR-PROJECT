"""Pre-demo check: is the key valid, does every configured model answer, what does it cost?

    cd backend
    GROQ_API_KEY=gsk_... python scripts/check_llm.py          # model ping only (any provider)
    GROQ_API_KEY=gsk_... python scripts/check_llm.py --full   # + one real generation
    GEMINI_API_KEY=...   python scripts/check_llm.py --list   # model ids your key can use
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.config import settings
from agent.llm import ProviderLLM


def list_models() -> int:
    """Print the model ids from the provider's OpenAI-compatible /models endpoint."""
    import httpx

    url = settings.llm_base_url.rstrip("/") + "/models"
    r = httpx.get(url, headers={"Authorization": f"Bearer {settings.llm_api_key}"}, timeout=30)
    if r.status_code != 200:
        print(f"  FAIL  GET {url} -> {r.status_code} {r.text[:200]}")
        return 1
    for model in sorted(m["id"].removeprefix("models/") for m in r.json().get("data", [])):
        print(f"  {model}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="also run one full generation")
    ap.add_argument("--list", action="store_true", help="list the model ids the provider offers")
    args = ap.parse_args()

    if not settings.llm_api_key:
        print("No API key: set GROQ_API_KEY, GEMINI_API_KEY, OPENROUTER_API_KEY or LLM_API_KEY + LLM_BASE_URL")
        return 1

    print(f"provider: {settings.llm_provider}  ({settings.llm_base_url})")
    if args.list:
        return list_models()
    llm = ProviderLLM()
    models = [("strong", settings.strong_model), ("cheap", settings.cheap_model), ("fallback", settings.fallback_model)]
    ok = True
    for role, model in models:
        chat = llm._fallback[1] if role == "fallback" else llm._models[role][1]
        t0 = time.monotonic()
        try:
            msg = chat.invoke([("user", "Reply with exactly: OK")])
            text = str(msg.content).strip()[:20]
            print(f"  OK    {role:<8} {model:<36} {time.monotonic() - t0:5.1f}s  reply={text!r}")
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  FAIL  {role:<8} {model:<36} {type(exc).__name__}: {str(exc)[:160]}")
    if not ok:
        print("\nFix: check the model ids your provider lists and set STRONG_MODEL / CHEAP_MODEL /")
        print("FALLBACK_MODEL (see --list), or check your credit balance with the provider")
        return 1

    if args.full:
        from agent.graph import main as run_graph

        print("\nFull run (architecture -> code -> validate -> self-heal):\n")
        return run_graph(["A blog API with posts and comments; users log in to write. Posts searchable by title."])
    return 0


if __name__ == "__main__":
    sys.exit(main())
