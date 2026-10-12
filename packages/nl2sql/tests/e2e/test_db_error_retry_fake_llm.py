"""A plan the database rejects is refined and retried, end to end.

The first plan passes the logical validator (every table, column and function
name is real) but calls ``ABS`` with two arguments, which SQLite refuses. That
used to end the run with a CRITICAL ``EXECUTOR_CRASH``. Now the adapter marks
the error as the query's own fault, the executor routes it to the refiner, and
the planner, told what the database said, returns the plan that answers.
"""
from __future__ import annotations

import json

import pytest

from nl2sql.testing.fake_llm import Rule

from .conftest import _base_env, run_cli
from .recordings_chinook import ANSWERABLE, COUNT_CUSTOMERS_DECOMPOSER, COUNT_CUSTOMERS_PLAN, count_customers_answer
from .test_trace_fake_llm import _serve

QUESTION = "How many customers do we have?"

# Validates (real table, real column, an allowed function), fails in SQLite.
BAD_ARITY_PLAN = {
    **COUNT_CUSTOMERS_PLAN,
    "select_items": [{"alias": "customer_count",
                      "expr": {"kind": "func", "func_name": "ABS",
                               "args": [{"kind": "column", "alias": "t1", "column_name": "CustomerId"},
                                        {"kind": "literal", "value": 2}]}}],
}

# The second planner prompt carries the database's words, which select the fix.
RULES = [
    ANSWERABLE,
    Rule("DecomposerResponse", COUNT_CUSTOMERS_DECOMPOSER),
    Rule("PlanModel", COUNT_CUSTOMERS_PLAN, when="wrong number of arguments"),
    Rule("PlanModel", BAD_ARITY_PLAN),
    Rule("AggregatedResponse", count_customers_answer),
    Rule("plain", "ABS takes one argument; count the customers with COUNT."),
]


def _prompt(call) -> str:
    return "\n".join(str(m.get("content") or "") for m in call["body"].get("messages", []))


@pytest.mark.e2e
def test_a_query_the_database_rejects_is_refined_and_the_second_plan_answers(demo_project, tmp_path):
    server = _serve(demo_project, RULES, "llm.db-retry.yaml")
    env = _base_env()
    env.update({"OPENAI_API_KEY": "sk-" + "fake-db-retry", "TRACE_MODE": "always", "TRACE_DIR": str(tmp_path),
                "SQL_AGENT_RETRY_BASE_DELAY_SEC": "0", "SQL_AGENT_RETRY_JITTER_SEC": "0"})
    try:
        r = run_cli(demo_project, env, "run", "--llm-config", "configs/llm.db-retry.yaml", QUESTION)
    finally:
        server.stop()

    assert r.returncode == 0, r.stdout + r.stderr
    [path] = list(tmp_path.glob("*.json"))
    doc = json.loads(path.read_text(encoding="utf-8"))
    result = doc["result"]
    [sub] = result["sub_queries"]

    assert result["status"] == "success", result["errors"]
    assert sub["retry_count"] == 1
    assert "COUNT(" in sub["sql"].upper()
    assert "There are 59 customers." in result["final_answer"]["summary"]

    # The first attempt's database error is kept, as a warning the retry recovered from.
    [warning] = [w for w in result["warnings"] if w["error_code"] == "EXECUTION_FAILED"]
    assert "wrong number of arguments" in warning["message"]
    assert "EXECUTOR_CRASH" not in json.dumps(result)

    # The refiner ran once, and the planner was told the database's words, not a trace.
    names = [c["name"] for c in server.calls]
    assert names.count("plain") == 1
    planner_calls = [c for c in server.calls if c["name"] == "PlanModel"]
    assert len(planner_calls) == 2
    retry_prompt = _prompt(planner_calls[1])
    assert "wrong number of arguments" in retry_prompt
    assert "Traceback" not in retry_prompt and "[SQL:" not in retry_prompt
