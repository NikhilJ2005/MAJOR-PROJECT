"""Pre-demo check: is the key valid, does every configured model answer, what does it cost?

    cd backend
    OPENROUTER_API_KEY=sk-or-... python scripts/check_llm.py          # model ping only
    OPENROUTER_API_KEY=sk-or-... python scripts/check_llm.py --full   # + one real generation
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.config import settings  # noqa: E402
from agent.llm import OpenRouterLLM  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="also run one full generation with a planted bug")
    args = ap.parse_args()

    if not settings.openrouter_api_key:
        print("OPENROUTER_API_KEY is not set")
        return 1

    llm = OpenRouterLLM()
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
        print("\nFix: check the model id on https://openrouter.ai/models and set STRONG_MODEL / CHEAP_MODEL /")
        print("FALLBACK_MODEL, or check credit balance at https://openrouter.ai/settings/credits")
        return 1

    if args.full:
        from agent.graph import main as run_graph

        print("\nFull run (architecture -> code -> validate -> self-heal):\n")
        return run_graph(["A blog API with posts and comments; users log in to write. Posts searchable by title.", "--fault"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
