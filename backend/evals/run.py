"""Benchmark runner: success rate, repairs, bugs found and fixed, tokens, cost, time.

    python -m evals.run                    # online: real model (LLM_PROVIDER + its API key)
    python -m evals.run --stack django     # any framework: fastapi (default), flask, django
    python -m evals.run --hard             # only the hard prompts (business rules, permissions, actions)
    python -m evals.run --only blog,todo
    python -m evals.run --offline          # reference specs + templates, no model (harness regression)
    python -m evals.run --fake             # the offline fake model, which makes typical mistakes

Ablation study (the paper's main table): every case runs once per configuration.

    python -m evals.run --ablation --hard

    full            everything on: dynamic healing, tool-using Reflector, edge-case tests
    fixed-3         the usual fixed retry count (3) instead of progress-based stopping
    no-tools        the Reflector repairs in one shot, without tools
    no-edge-tests   without the spec-derived edge-case tests

After each successful run the edge-case tests are also run as a held-out check. A configuration
that skipped them shows how many bugs would have shipped ("escaped bugs").

Results go to evals/results/: a Markdown report, JSON, and a CSV for charts.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

from agent import llm as llm_mod
from agent.codegen import render_project
from agent.graph import build_graph, initial_state
from agent.sandbox import get_sandbox
from agent.state import ProjectSpec
from agent.testwriter import EDGE_TESTS

HERE = Path(__file__).parent

CONFIGS: dict[str, dict] = {
    "full": {},
    "fixed-3": {"fixed_repairs": 3},
    "no-tools": {"tools": False},
    "no-edge-tests": {"edge_tests": False},
}
FAKE_MISTAKES = ["typo", "swallow_fk", None]  # what the fake model gets wrong, case by case


def escaped_bugs(values: dict) -> int:
    """Held-out check: how many spec-derived edge-case tests fail on the final code."""
    if values.get("status") != "succeeded" or not values.get("spec"):
        return 0
    spec = ProjectSpec.model_validate(values["spec"])
    files = {**values["files"], EDGE_TESTS: render_project(spec)[EDGE_TESTS]}
    ok, log = get_sandbox().run_tests(files, spec.stack, [EDGE_TESTS])
    found = re.search(r"(\d+) failed", log)
    return 0 if ok else int(found.group(1)) if found else 1


def run_case(graph, case: dict, mode: str, stack: str, config: str = "full", mistake: str | None = None) -> dict:
    options = {"codegen_mode": "template" if mode == "offline" else "llm", "stack": stack, **CONFIGS[config]}
    if mode == "fake":
        from agent.fake_llm import FakeLLM

        llm_mod.set_llm(FakeLLM(spec=case["spec"], mistake=mistake))
    state = initial_state(case["prompt"], options, case["spec"] if mode == "offline" else None)
    run = {"configurable": {"thread_id": state["run_id"]}}
    t0 = time.monotonic()
    error = None
    try:
        list(graph.stream(state, run, stream_mode="updates"))
    except Exception as exc:  # noqa: BLE001 - an eval records failures, it doesn't stop on them
        error = f"{type(exc).__name__}: {exc}"
    values = graph.get_state(run).values
    seconds = round(time.monotonic() - t0, 1)
    usage = values.get("usage") or {}
    bugs = values.get("bugs") or []
    spec = values.get("spec") or {}
    tool_calls = [c for e in values.get("ledger", []) for c in e.get("tools", [])]
    return {
        "config": config,
        "id": case["id"],
        "stack": stack,
        "status": "error" if error else values.get("status"),
        "repairs": values.get("iteration", 0),
        "stop_reason": values.get("stop_reason") or "",
        "requirements": len(spec.get("requirements", [])),
        "entities": len(spec.get("entities", [])),
        "actions": len(spec.get("actions", [])),
        "bugs_found": len(bugs),
        "bugs_fixed": sum(1 for b in bugs if b.get("fixed")),
        "found_by": dict(Counter(b.get("found_by", "?") for b in bugs)),
        "escaped_bugs": escaped_bugs(values) if not error else 0,
        "tool_calls": len(tool_calls),
        "failed_gate": "" if values.get("status") == "succeeded" else (values.get("validation") or {}).get("gate", ""),
        "files": len(values.get("files") or {}),
        "tokens": usage.get("total_tokens", 0),
        "cost_usd": usage.get("cost_usd", 0.0),
        "seconds": seconds,
        "error": error or "",
    }


def summarize(rows: list[dict]) -> dict:
    ok = [r for r in rows if r["status"] == "succeeded"]

    def mean(key: str, digits: int = 2) -> float:
        return round(statistics.mean(r[key] for r in rows), digits) if rows else 0

    return {
        "cases": len(rows),
        "passed": len(ok),
        "pass_rate": round(len(ok) / len(rows), 3) if rows else 0,
        "first_try": sum(1 for r in ok if r["repairs"] == 0),
        "avg_repairs": mean("repairs"),
        "bugs_found": sum(r["bugs_found"] for r in rows),
        "bugs_fixed": sum(r["bugs_fixed"] for r in rows),
        "escaped_bugs": sum(r["escaped_bugs"] for r in rows),
        "tool_calls": sum(r["tool_calls"] for r in rows),
        "avg_tokens": round(mean("tokens", 0)),
        "avg_cost_usd": mean("cost_usd", 4),
        "avg_seconds": mean("seconds", 1),
    }


def to_markdown(title: str, summaries: dict[str, dict], rows: list[dict]) -> str:
    lines = [f"# VibeStack eval: {title} ({datetime.now():%Y-%m-%d %H:%M})", ""]
    lines += [
        ("| configuration | pass rate | first try | avg repairs | bugs found / fixed | escaped bugs | tool calls "
         "| avg tokens | avg cost $ | avg time s |"),
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, s in summaries.items():
        lines.append(
            f"| {name} | {s['passed']}/{s['cases']} ({s['pass_rate']:.0%}) | {s['first_try']} | {s['avg_repairs']} "
            f"| {s['bugs_found']} / {s['bugs_fixed']} | {s['escaped_bugs']} | {s['tool_calls']} | {s['avg_tokens']} "
            f"| {s['avg_cost_usd']} | {s['avg_seconds']} |"
        )
    lines += ["", "## Every run", "",
              "| configuration | case | status | repairs | bugs found/fixed | escaped | stop reason | tokens | time s |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['config']} | {r['id']} | {r['status']} | {r['repairs']} | {r['bugs_found']}/{r['bugs_fixed']} "
            f"| {r['escaped_bugs']} | {r['stop_reason']} | {r['tokens']} | {r['seconds']} |"
        )
    return "\n".join(lines) + "\n"


def write_results(title: str, summaries: dict[str, dict], rows: list[dict]) -> Path:
    out_dir = HERE / "results"
    out_dir.mkdir(exist_ok=True)
    base = out_dir / f"{datetime.now():%Y%m%d-%H%M%S}-{title}"
    base.with_suffix(".md").write_text(to_markdown(title, summaries, rows))
    base.with_suffix(".json").write_text(json.dumps({"summary": summaries, "rows": rows}, indent=2))
    with base.with_suffix(".csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({**r, "found_by": json.dumps(r["found_by"])} for r in rows)
    return base


def main() -> None:
    ap = argparse.ArgumentParser()
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--offline", action="store_true", help="reference specs + templates, no model")
    group.add_argument("--fake", action="store_true", help="the offline fake model (makes typical mistakes)")
    ap.add_argument("--only", help="comma-separated case ids")
    ap.add_argument("--stack", choices=["fastapi", "flask", "django"], default="fastapi")
    ap.add_argument("--hard", action="store_true", help="only the hard prompts")
    ap.add_argument("--ablation", action="store_true", help="run every configuration in CONFIGS")
    args = ap.parse_args()

    cases = yaml.safe_load((HERE / "prompts.yaml").read_text())
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    if args.hard:
        cases = [c for c in cases if c.get("hard")]
    mode = "offline" if args.offline else "fake" if args.fake else "online"
    if mode == "offline":
        llm_mod.set_llm(None)
    elif mode == "online" and llm_mod.get_llm() is None:
        raise SystemExit("no model configured (set LLM_PROVIDER and its API key); use --offline or --fake")

    graph = build_graph()
    configs = list(CONFIGS) if args.ablation else ["full"]
    rows = []
    for config in configs:
        for i, case in enumerate(cases):
            row = run_case(graph, case, mode, args.stack, config, FAKE_MISTAKES[i % len(FAKE_MISTAKES)])
            rows.append(row)
            print(f"{config:>13} {row['id']:>12}: {row['status']:<10} repairs={row['repairs']} "
                  f"escaped={row['escaped_bugs']} tokens={row['tokens']} {row['seconds']}s"
                  + (f"  {row['error']}" if row["error"] else ""))

    summaries = {c: summarize([r for r in rows if r["config"] == c]) for c in configs}
    title = f"{mode}-{args.stack}" + ("-hard" if args.hard else "") + ("-ablation" if args.ablation else "")
    base = write_results(title, summaries, rows)
    print(json.dumps(summaries, indent=2))
    print(f"results: {base}.md / .json / .csv")


if __name__ == "__main__":
    main()
