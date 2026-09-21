import pytest

from .conftest import run_cli
from .recordings_chinook import (
    RULES_ALBUMS_PER_ARTIST,
    RULES_COUNT_CUSTOMERS,
    RULES_GENRE_SALES_GPT4O,
    RULES_JAZZ_TRACKS,
    RULES_TOP_CUSTOMERS,
    RULES_TOP_GENRE,
)


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


@pytest.mark.e2e
def test_top_n_by_an_aggregate_validates_and_sorts_descending(demo_project, fake_llm):
    """"Which genre sells the most tracks?" -- one of the guided questions.

    Its ORDER BY is a function call. The validator used to hand that to
    `qualify()` unwrapped and abort with VALIDATOR_CRASH, so no "top N by
    <aggregate>" plan ever reached the generator; the generator then dropped
    the direction, so the answer would have come back ascending anyway.
    """
    server, env = fake_llm(RULES_TOP_GENRE)
    r = run_cli(demo_project, env, "run", "--llm-config", "configs/llm.fake.yaml",
                "Which genre sells the most tracks?")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Pipeline Errors" not in r.stdout, r.stdout
    assert "DESC" in r.stdout.upper()
    # Rock leads with 835 sold invoice lines; ascending would have started at 1.
    assert "Top genre: Rock with 835" in r.stdout
    assert [c["name"] for c in server.calls] == ["DecomposerResponse", "PlanModel", "AggregatedResponse"]


@pytest.mark.e2e
def test_an_equality_filter_on_an_unsampled_value_validates(demo_project, fake_llm):
    """`Genre.Name = 'Jazz'` -- a correct filter on a value outside the sample.

    The adapter records five sample values per text column. The validator used
    to read those five as the column's whole domain and reject this filter with
    INVALID_PLAN_STRUCTURE, which the refiner could not recover from.
    """
    server, env = fake_llm(RULES_JAZZ_TRACKS)
    r = run_cli(demo_project, env, "run", "--llm-config", "configs/llm.fake.yaml",
                "How many jazz tracks are there?")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Pipeline Errors" not in r.stdout, r.stdout
    assert "not found in stats" not in r.stdout
    assert "130" in r.stdout
    assert [c["name"] for c in server.calls] == ["DecomposerResponse", "PlanModel", "AggregatedResponse"]


@pytest.mark.e2e
def test_a_plan_gpt4o_wrote_generates_sql_that_runs(demo_project, fake_llm):
    """Replays, unedited, the plan gpt-4o produced for a guided question.

    It lists Genre first, joins Track to it with Genre as ``right_alias``, and
    sums ``UnitPrice * Quantity``. The generator used to join Genre to itself,
    never add InvoiceLine, and render the product as a function named ``*``,
    so SQLite rejected the SQL with ``near "(": syntax error``.
    """
    server, env = fake_llm(RULES_GENRE_SALES_GPT4O)
    r = run_cli(demo_project, env, "run", "--llm-config", "configs/llm.fake.yaml",
                "Which genre sells the most tracks?")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Pipeline Errors" not in r.stdout, r.stdout
    assert "InvoiceLine AS t3" in r.stdout
    assert "t3.UnitPrice * t3.Quantity" in r.stdout
    # Rock grosses 826.65, the most of any genre.
    assert "Top genre: Rock with 826.65" in r.stdout
    assert [c["name"] for c in server.calls] == ["DecomposerResponse", "PlanModel", "AggregatedResponse"]


@pytest.mark.e2e
def test_a_denied_query_stops_before_aggregation_and_synthesis(demo_project, fake_llm):
    """``viewer`` may not read Customer or Invoice.

    The denial itself always worked; the run then went on to the aggregator,
    which failed on the missing artifact, and to the answer synthesizer, which
    spent another LLM call. Neither has anything to work on.
    """
    server, env = fake_llm(RULES_TOP_CUSTOMERS)
    r = run_cli(demo_project, env, "run", "--role", "viewer", "--llm-config", "configs/llm.fake.yaml",
                "Who are the top customers by total spend?")
    assert "SECURITY_VIOLATION" in r.stdout, r.stdout + r.stderr
    assert "AGGREGATOR_FAILED" not in r.stdout
    assert "AggregatedResponse" not in [c["name"] for c in server.calls]
