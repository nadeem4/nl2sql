import pytest

from .conftest import run_cli
from .recordings_chinook import RULES_ALBUMS_PER_ARTIST, RULES_COUNT_CUSTOMERS


@pytest.mark.e2e
def test_count_customers_end_to_end(demo_project, fake_llm):
    server, env = fake_llm(RULES_COUNT_CUSTOMERS)
    r = run_cli(demo_project, env, "run", "--llm-config", "configs/llm.fake.yaml", "How many customers are there?")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "COUNT(" in r.stdout and "Customer" in r.stdout
    assert "59" in r.stdout
    assert [c["name"] for c in server.calls] == ["DecomposerResponse", "PlanModel", "AggregatedResponse"]


@pytest.mark.e2e
def test_no_exec_prints_sql_and_never_executes(demo_project, fake_llm):
    server, env = fake_llm(RULES_COUNT_CUSTOMERS)
    r = run_cli(demo_project, env, "run", "--no-exec", "--llm-config", "configs/llm.fake.yaml", "How many customers are there?")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "COUNT(" in r.stdout
    assert "59" not in r.stdout
    assert "AggregatedResponse" not in [c["name"] for c in server.calls]


@pytest.mark.e2e
def test_a_foreign_key_join_passes_validation_and_executes(demo_project, fake_llm):
    """Chinook declares real foreign keys, so a join must reach the generator.

    The manufacturing demo declared none, so `LogicalValidatorNode`'s
    relationship check rejected every join, and no end-to-end test could tell
    a working relationship match from a broken one.
    """
    server, env = fake_llm(RULES_ALBUMS_PER_ARTIST)
    r = run_cli(demo_project, env, "run", "--llm-config", "configs/llm.fake.yaml",
                "How many albums does each artist have?")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "JOIN" in r.stdout.upper()
    assert "does not match any allowed relationship" not in r.stdout
    assert "Pipeline Errors" not in r.stdout, r.stdout
    assert [c["name"] for c in server.calls] == ["DecomposerResponse", "PlanModel", "AggregatedResponse"]
