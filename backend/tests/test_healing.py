import pytest

from agent.healing import classify_error, locate_failing_file, related_files
from agent.state import ProjectSpec

SPEC = ProjectSpec.model_validate({"project_name": "x", "entities": [{"name": "Post", "fields": [{"name": "title"}]}]})
FILES = {p: "" for p in ["app/main.py", "app/routers/post.py", "app/models/post.py", "app/schemas/post.py", "app/database.py"]}


@pytest.mark.parametrize(
    "log,expected",
    [
        ("  File \"app/models/post.py\", line 3\n    x = (\nSyntaxError: '(' was never closed", "syntax"),
        ("ImportError: cannot import name 'Sesion' from 'sqlalchemy.orm'", "import"),
        ("ModuleNotFoundError: No module named 'passlib'", "dependency"),
        ("ModuleNotFoundError: No module named 'app.models.foo'", "import"),
        ("sqlalchemy.exc.NoReferencedTableError: Foreign key could not find table", "orm"),
        ("NameError: name 'Post' is not defined", "runtime"),
        ("FAILED tests/test_smoke.py::test_crud[Post] - assert 200 == 201", "contract"),
        ("TimeoutError: gate exceeded 60s", "timeout"),
        ("something odd", "unknown"),
    ],
)
def test_classify(log, expected):
    assert classify_error(log) == expected


def test_locate_prefers_deepest_app_frame():
    log = 'File "app/main.py", line 8, in <module>\n  File "app/routers/post.py", line 4, in <module>\nImportError: x'
    assert locate_failing_file(log, FILES, SPEC) == "app/routers/post.py"


def test_locate_from_failing_smoke_test():
    log = "FAILED tests/test_smoke.py::test_crud[Post] - assert 200 == 201"
    assert locate_failing_file(log, FILES, SPEC) == "app/routers/post.py"


def test_failing_rule_test_points_at_the_service_for_actions():
    from agent.healing import file_for_test
    from tests.specs import SHOP

    shop = ProjectSpec.model_validate(SHOP)
    assert file_for_test("r = client.post(f'/orders/{oid}/checkout')", shop) == "app/services/order.py"
    assert file_for_test("r = client.get('/products/', params={'q': 'x'})", shop) == "app/routers/product.py"
    assert file_for_test("r = client.get('/health')", shop) is None


def test_related_files_are_entity_siblings_only():
    assert related_files("app/routers/post.py", FILES) == ["app/models/post.py", "app/schemas/post.py", "app/database.py"]


# --------------------------------------------------------------------------- dynamic stopping

from agent.healing import error_signature, failing_count, repair_rung, should_continue


def _h(gate, failing=None, fp="a", ok=False, sig=None):
    return {"ok": ok, "gate": gate, "failing": failing, "signature": sig or gate, "fingerprint": fp}


def _go(history, llm=True, tokens=0):
    return should_continue(history, tokens_used=tokens, token_budget=1000, llm_available=llm, safety_limit=12)


def test_failing_count_reads_pytest_summary():
    assert failing_count("== 2 failed, 5 passed in 1.2s ==") == 2
    assert failing_count("== 1 failed, 1 error in 0.3s ==") == 2
    assert failing_count("== 7 passed in 1s ==") == 0
    assert failing_count("ImportError: boom") is None


def test_signature_ignores_line_numbers():
    a = error_signature("import", 'File "app/x.py", line 3\nImportError: cannot import name Foo')
    b = error_signature("import", 'File "app/x.py", line 9\nImportError: cannot import name Foo')
    assert a == b


def test_first_failure_tries_the_cheap_model():
    assert _go([_h("import")]) == (True, "next repair level: cheap")
    assert repair_rung([_h("import")], llm_available=True) == "cheap"


def test_progress_keeps_going_and_resets_to_cheap():
    history = [_h("import", fp="a"), _h("tests", failing=3, fp="b"), _h("tests", failing=1, fp="c")]
    keep, reason = _go(history)
    assert keep and reason.startswith("progress")
    assert repair_rung(history, True) == "cheap"


def test_each_stall_climbs_one_rung_then_stops():
    h = [_h("tests", 2, "a"), _h("tests", 2, "b")]
    assert repair_rung(h, True) == "strong"
    h.append(_h("tests", 2, "c"))
    assert repair_rung(h, True) == "template"
    h.append(_h("tests", 3, "d"))
    keep, reason = _go(h)
    assert not keep and "no progress" in reason


def test_offline_has_only_the_template_rung():
    assert repair_rung([_h("import")], llm_available=False) == "template"
    keep, _ = _go([_h("import", fp="a"), _h("import", fp="b")], llm=False)
    assert not keep


def test_circles_stop_when_the_same_change_repeats():
    # Going back to an earlier version once is allowed (a bad repair rolled back) ...
    keep, _ = _go([_h("tests", 2, "a"), _h("tests", 1, "b", sig="x"), _h("tests", 2, "a")])
    assert keep
    # ... the same change happening again is going in circles.
    keep, reason = _go([_h("tests", 2, "a"), _h("tests", 1, "b", sig="x"), _h("tests", 2, "a"), _h("tests", 1, "b", sig="x")])
    assert not keep and "circles" in reason


def test_improving_on_the_previous_attempt_is_progress():
    from agent.healing import made_progress, stalls

    # best was 3 failing; a template restore made it 10; then 6, then 4: each repair improved -> no stalls
    history = [_h("tests", 3, "a", sig="s"), _h("tests", 10, "b", sig="t"), _h("tests", 6, "c", sig="t"), _h("tests", 4, "d", sig="t")]
    assert not made_progress(history[1], history[:1])  # worse than before
    assert made_progress(history[2], history[:2]) and made_progress(history[3], history[:3])
    assert stalls(history) == 0


def test_fixing_some_tests_counts_even_when_others_break():
    from agent.healing import made_progress

    before = {**_h("tests", 2, "a", sig="s"), "failed_tests": ["t::a", "t::b"]}
    after = {**_h("tests", 2, "b", sig="s"), "failed_tests": ["t::b", "t::c"]}
    assert made_progress(after, [before])


def test_busy_machine_is_retried_but_not_forever():
    from agent.healing import best_index, regressed, stalls

    busy = {**_h("tests", None, "a", sig="timeout"), "transient": True}
    history = [_h("tests", 2, "a"), busy, busy]
    assert stalls(history) == 0 and best_index(history) == 0 and not regressed(history)
    keep, reason = _go(history)
    assert keep and "busy" in reason
    keep, reason = _go([_h("tests", 2, "a"), busy, busy, busy, busy])
    assert not keep and "not a bug" in reason


def test_a_worse_result_is_a_regression_to_roll_back():
    from agent.healing import best_index, regressed

    history = [_h("tests", 5, "a"), _h("tests", 1, "b"), _h("import", None, "c")]
    assert best_index(history) == 1 and regressed(history)


def test_budget_stops_but_never_before_the_first_repair():
    keep, _ = _go([_h("import")], tokens=5000)
    assert keep  # the first repair always happens, however much the design and code cost
    keep, reason = _go([_h("import"), _h("boot")], tokens=5000)
    assert not keep and "budget" in reason


def test_money_budget_stops_a_long_repair():
    from agent.llm import budget_left

    assert budget_left({"cost_usd": 0.4, "total_tokens": 10}) is None
    reason = budget_left({"cost_usd": 99.0})
    assert reason.startswith("cost budget used up")
    keep, why = should_continue([_h("import"), _h("boot")], tokens_used=0, token_budget=10**9, llm_available=True,
                                safety_limit=12, budget_problem=reason)
    assert not keep and why == reason


def test_safety_limit_is_only_a_seatbelt():
    history = [_h("tests", 100 - i, fp=str(i)) for i in range(13)]  # always improving
    keep, reason = _go(history)
    assert not keep and "safety limit" in reason


def test_a_new_error_counts_as_progress_but_a_returning_one_does_not():
    a, b = _h("import", fp="1", sig="import: A"), _h("import", fp="2", sig="import: B")
    assert repair_rung([a, b], True) == "cheap"  # A was fixed, B is new
    assert repair_rung([a, b, _h("import", fp="3", sig="import: A")], True) == "strong"  # A is back


def test_a_repair_that_changes_nothing_climbs_the_ladder_instead_of_stopping():
    keep, reason = _go([_h("tests", 2, "a"), _h("tests", 2, "a")])
    assert keep and reason == "next repair level: strong"


def test_sandbox_limits_are_not_code_bugs():
    from agent.bughunt import problems_in
    from agent.healing import classify_error

    log = "Traceback ...\nRuntimeError: can't start new thread\nTimeoutError: /health did not respond"
    assert classify_error(log) == "sandbox"
    assert problems_in({"gate": "boot", "log": log}) == []


def test_several_failing_services_are_targeted_in_one_attempt():
    from agent.healing import failing_targets
    from tests.specs import ESCROW

    spec = ProjectSpec.model_validate(ESCROW)
    rules = (
        'def test_fund(client):\n    client.post("/milestones/1/fund")\n\n'
        'def test_bid(client):\n    client.get("/bids/")\n\n'
        'def test_submit(client):\n    client.post("/milestones/1/submit")\n'
    )
    files = {"tests/test_rules.py": rules, "app/services/milestone.py": "", "app/services/bid.py": "",
             "app/routers/bid.py": ""}
    log = ("FAILED tests/test_rules.py::test_fund - assert 500 == 200\n"
           "FAILED tests/test_rules.py::test_bid - assert 500 == 200\n"
           "FAILED tests/test_rules.py::test_submit - assert 500 == 200\n")
    # Bid has no actions in this design, so its router failure points at app/services/bid.py (its business logic).
    assert failing_targets(log, files, spec, "app/services/milestone.py") == ["app/services/milestone.py", "app/services/bid.py"]
    assert failing_targets(log, files, spec, None, limit=1) == ["app/services/milestone.py"]
