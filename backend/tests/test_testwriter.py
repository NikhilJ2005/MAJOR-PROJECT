from agent.analysis import check_coverage
from agent.state import ProjectSpec
from agent.testwriter import get_test_source, parse_dispute, remove_test, valid_test_file
from tests.specs import SHOP

SOURCE = '''import pytest


def test_a(client):
    assert 1


@pytest.mark.parametrize("x", [1, 2])
def test_b(client, x):
    assert x


def test_c(client):
    assert 3
'''


def test_extract_and_remove_one_test():
    assert get_test_source(SOURCE, "test_b[1]").startswith("def test_b")
    out = remove_test(SOURCE, "test_b[2]")
    assert "test_b" not in out and "parametrize" not in out
    assert "def test_a" in out and "def test_c" in out
    assert valid_test_file(out)


def test_invalid_test_files_are_rejected():
    assert not valid_test_file("def test_x(:\n")
    assert not valid_test_file("x = 1\n")


def test_dispute_is_parsed():
    assert parse_dispute("DISPUTE: test_double_checkout: the requirements allow it") == (
        "test_double_checkout", "the requirements allow it")
    assert parse_dispute("DIAGNOSIS: fixed it") is None


def test_coverage_finds_uncovered_requirements():
    spec = ProjectSpec.model_validate(SHOP)
    assert check_coverage(spec) == []
    spec.requirements.append(spec.requirements[0].model_copy(update={"id": "R9"}))
    assert check_coverage(spec) == ["R9"]
