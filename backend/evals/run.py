"""Benchmark runner: generation success rate, healing iterations, tokens, cost, latency.

    python -m evals.run                  # online: LLM spec + LLM codegen (needs OPENROUTER_API_KEY)
    python -m evals.run --offline        # reference specs + templates, no LLM (harness regression)
    python -m evals.run --fault          # inject a bug into every project to measure healing
    python -m evals.run --only blog,todo
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

import yaml

from agent import llm as llm_mod
from agent.graph import build_graph, initial_state

HERE = Path(__file__).parent


def run_case(graph, case: dict, offline: bool, fault: bool) -> dict:
    options = {"inject_fault": fault, "codegen_mode": "template" if offline else "llm"}
    state = initial_state(case["prompt"], options, case["spec"] if offline else None)
    config = {"configurable": {"thread_id": state["run_id"]}}
    t0 = time.monotonic()
    error = None
    try:
        list(graph.stream(state, config, stream_mode="updates"))
    except Exception as exc:  # noqa: BLE001 - an eval records failures, it doesn't stop on them
        error = f"{type(exc).__name__}: {exc}"
    values = graph.get_state(config).values
    usage = values.get("usage") or {}
    return {
        "id": case["id"],
        "status": "error" if error else values.get("status"),
        "heal_iterations": values.get("iteration", 0),
        "failed_gate": None if values.get("status") == "succeeded" else (values.get("validation") or {}).get("gate"),
        "entities": len((values.get("spec") or {}).get("entities", [])),
        "files": len(values.get("files") or {}),
        "tokens": usage.get("total_tokens", 0),
        "cost_usd": usage.get("cost_usd", 0.0),
        "seconds": round(time.monotonic() - t0, 1),
        "error": error,
    }


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["status"] == "succeeded"]
    return {
        "cases": len(rows),
        "passed": len(ok),
        "pass_rate": round(len(ok) / len(rows), 3) if rows else 0,
        "first_try_pass": sum(1 for r in ok if r["heal_iterations"] == 0),
        "avg_heal_iterations": round(statistics.mean(r["heal_iterations"] for r in rows), 2) if rows else 0,
        "avg_tokens": round(statistics.mean(r["tokens"] for r in rows)) if rows else 0,
        "avg_cost_usd": round(statistics.mean(r["cost_usd"] for r in rows), 4) if rows else 0,
        "avg_seconds": round(statistics.mean(r["seconds"] for r in rows), 1) if rows else 0,
    }


def to_markdown(mode: str, summary: dict, rows: list[dict]) -> str:
    lines = [
        f"# VibeStack eval: {mode} ({datetime.now():%Y-%m-%d %H:%M})",
        "",
        f"**Pass rate {summary['passed']}/{summary['cases']} ({summary['pass_rate']:.0%})**, "
        f"first-try {summary['first_try_pass']}, avg heal iterations {summary['avg_heal_iterations']}, "
        f"avg tokens {summary['avg_tokens']}, avg cost ${summary['avg_cost_usd']}, avg time {summary['avg_seconds']}s",
        "",
        "| case | status | heal iters | failed gate | entities | files | tokens | cost $ | time s |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['status']} | {r['heal_iterations']} | {r['failed_gate'] or ''} | {r['entities']} "
            f"| {r['files']} | {r['tokens']} | {r['cost_usd']:.4f} | {r['seconds']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--fault", action="store_true")
    ap.add_argument("--only", help="comma-separated case ids")
    args = ap.parse_args()

    cases = yaml.safe_load((HERE / "prompts.yaml").read_text())
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if args.offline:
        llm_mod.set_llm(None)
    elif llm_mod.get_llm() is None:
        raise SystemExit("OPENROUTER_API_KEY not set; use --offline for a no-LLM harness run")

    graph = build_graph()
    rows = []
    for case in cases:
        row = run_case(graph, case, args.offline, args.fault)
        rows.append(row)
        print(f"{row['id']:>10}: {row['status']:<10} heal={row['heal_iterations']} tokens={row['tokens']} {row['seconds']}s"
              + (f"  {row['error']}" if row["error"] else ""))

    mode = ("offline" if args.offline else "online") + ("+fault" if args.fault else "")
    summary = summarize(rows)
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"{stamp}-{mode}.md").write_text(to_markdown(mode, summary, rows))
    (out_dir / f"{stamp}-{mode}.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
