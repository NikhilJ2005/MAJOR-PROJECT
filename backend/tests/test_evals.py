"""The ablation switches used by evals/run.py, and its held-out escaped-bug check."""

import yaml

from agent import llm as llm_mod
from agent.graph import build_graph
from evals.run import HERE, run_case


def _case(case_id):
    return next(c for c in yaml.safe_load((HERE / "prompts.yaml").read_text()) if c["id"] == case_id)


def test_without_edge_tests_a_bug_escapes_and_the_held_out_check_counts_it():
    graph = build_graph()
    try:
        full = run_case(graph, _case("blog"), "fake", "fastapi", "full", mistake="swallow_fk")
        blind = run_case(graph, _case("blog"), "fake", "fastapi", "no-edge-tests", mistake="swallow_fk")
    finally:
        llm_mod.set_llm(None)
    assert full["status"] == blind["status"] == "succeeded"
    assert full["found_by"].get("edge-case tests") and full["escaped_bugs"] == 0
    assert blind["escaped_bugs"] > 0  # it "passed", with a bug inside


def test_fixed_retry_policy_stops_after_n_repairs_even_while_progressing():
    from agent.nodes import _keep_healing

    history = [{"fingerprint": str(i), "gate": "tests", "failed": 5 - i} for i in range(4)]
    assert _keep_healing({"heal_history": history[:3], "options": {"fixed_repairs": 3}})[0] is True
    keep, reason = _keep_healing({"heal_history": history, "options": {"fixed_repairs": 3}})
    assert keep is False and reason == "fixed limit of 3 repair attempts reached"
