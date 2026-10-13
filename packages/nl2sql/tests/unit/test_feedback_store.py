"""The feedback table: one rating per run, kept beside the schema snapshots.

A row holds the question, the SQL and the run's signals, never result rows,
sample values or keys. A run the role was refused keeps no SQL.
"""
from __future__ import annotations

import json

import pytest

from nl2sql.feedback import FeedbackStore, run_record, run_signals
from nl2sql.schema import SqliteSchemaStore

TRACE_ID = "0b8f7d2e-1111-4222-8333-944455556666"


def _result(**overrides):
    body = {
        "trace_id": TRACE_ID,
        "status": "success",
        "errors": [],
        "sub_queries": [{
            "id": "sq1", "sql": "SELECT COUNT(*) AS n FROM Customer", "retry_count": 1,
            "plan_source": "llm",
            "validation": [{"name": "policy", "passed": True, "message": "ok"}],
            "rows": {"columns": ["n"], "rows": [[59]], "total_rows": 1},
        }],
        "final_answer": {"summary": "There are 59 customers."},
        "usage": {"calls": [
            {"node": "decomposer", "model": "gpt-4.1-mini"},
            {"node": "ast_planner", "model": "gpt-4.1"},
            {"node": "ast_planner", "model": "gpt-4.1"},
        ]},
    }
    body.update(overrides)
    return body


CONFIGS = {"default": {"provider": "openai", "model": "gpt-4.1-mini"},
           "astplanner": {"provider": "anthropic", "model": "claude-haiku-4-5"}}


def test_a_record_holds_the_question_the_sql_and_the_models_per_node():
    record = run_record(_result(), question="How many customers?", role="analyst",
                        llm_configs=CONFIGS, engine_version="1.2.3 (abc1234)")

    assert record["trace_id"] == TRACE_ID
    assert record["question"] == "How many customers?"
    assert record["role"] == "analyst"
    assert record["status"] == "success"
    assert record["sql"] == ["SELECT COUNT(*) AS n FROM Customer"]
    assert record["models"] == {
        "decomposer": {"provider": "openai", "model": "gpt-4.1-mini"},
        "ast_planner": {"provider": "anthropic", "model": "gpt-4.1"},
    }
    assert record["engine_version"] == "1.2.3 (abc1234)"
    assert record["retries"] == 1 and record["plan_cache_hits"] == 0 and record["sub_queries"] == 1


def test_a_record_never_holds_rows_the_answer_or_error_messages():
    result = _result(errors=[{"node": "executor", "message": "table Secret has value 4111-1111",
                              "error_code": "DB_EXECUTION_ERROR", "severity": "ERROR"}])

    record = run_record(result, question="q", role="admin", llm_configs={}, engine_version="x")

    text = json.dumps(record)
    assert "59" not in text  # neither the row nor the summary
    assert "4111" not in text and "Secret" not in text
    assert record["error_codes"] == ["DB_EXECUTION_ERROR"]


def test_a_refused_run_keeps_no_sql():
    result = _result(status="error", errors=[{"node": "logical_validator", "message": "no access to Employee",
                                              "error_code": "SECURITY_VIOLATION", "severity": "ERROR"}])

    record = run_record(result, question="Who earns most?", role="viewer", llm_configs={}, engine_version="x")

    assert record["sql"] == []
    assert record["error_codes"] == ["SECURITY_VIOLATION"]


def test_signals_count_validator_failures_from_a_trace_when_there_is_one():
    trace = {"nodes": [
        {"node": "logical_validator", "status": "error", "errors": [{"error_code": "COLUMN_NOT_FOUND"}]},
        {"node": "refiner", "status": "ok", "errors": []},
        {"node": "logical_validator", "status": "ok", "errors": []},
    ]}

    assert run_signals(_result())["validator_failures"] == 0
    assert run_signals(_result(), trace)["validator_failures"] == 1
    failed_check = _result(sub_queries=[{"sql": "", "validation": [{"name": "policy", "passed": False}]}])
    assert run_signals(failed_check)["validator_failures"] == 1


def test_store_saves_lists_and_replaces_a_rating(tmp_path):
    store = FeedbackStore(tmp_path / "schema_store.db")
    record = run_record(_result(), question="How many customers?", role="admin", llm_configs={},
                        engine_version="x")

    store.save(record, rating="up", note=None)
    store.save(record, rating="down", note="wrong number")
    rows = store.list()

    assert len(rows) == 1
    assert rows[0]["rating"] == "down" and rows[0]["note"] == "wrong number"
    assert rows[0]["sql"] == ["SELECT COUNT(*) AS n FROM Customer"]
    assert rows[0]["models"] == {"decomposer": {"provider": None, "model": "gpt-4.1-mini"},
                                 "ast_planner": {"provider": None, "model": "gpt-4.1"}}
    assert store.counts() == {"up": 0, "down": 1}
    store.close()


def test_store_refuses_a_bad_rating(tmp_path):
    store = FeedbackStore(tmp_path / "schema_store.db")
    record = run_record(_result(), question="q", role="admin", llm_configs={}, engine_version="x")
    with pytest.raises(ValueError):
        store.save(record, rating="meh", note=None)
    store.close()


def test_the_table_lives_in_the_schema_store_and_clear_leaves_the_rest(tmp_path):
    path = tmp_path / "data" / "schema_store.db"
    SqliteSchemaStore(path=path).close()
    store = FeedbackStore(path)
    store.save(run_record(_result(), question="q", role="admin", llm_configs={}, engine_version="x"),
               rating="up", note=None)

    assert store.clear() == 1
    assert store.list() == []
    tables = {r[0] for r in store._connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"feedback", "schema_snapshots", "plan_cache"} <= tables
    store.close()


def test_a_record_holds_the_datasources_and_where_the_answer_came_from():
    generated = _result(sub_queries=[{"sql": "SELECT 1", "datasource_id": "chinook"},
                                     {"sql": "SELECT 2", "datasource_id": "chinook"}],
                        source={"type": "generated"})
    verified = _result(sub_queries=[{"sql": "SELECT 1", "datasource_id": "chinook", "plan_source": "verified"}],
                       source={"type": "verified", "id": "top_customers", "datasource_id": "chinook"})

    record = run_record(generated, question="q", role="admin", llm_configs={}, engine_version="x")
    assert record["datasource_ids"] == ["chinook"]
    assert record["source"] == "generated"
    assert run_record(verified, question="q", role="admin", llm_configs={},
                      engine_version="x")["source"] == "verified"


def test_the_store_keeps_the_datasources_and_the_source(tmp_path):
    store = FeedbackStore(tmp_path / "schema_store.db")
    record = run_record(_result(sub_queries=[{"sql": "SELECT 1", "datasource_id": "chinook"}],
                                source={"type": "generated"}),
                        question="q", role="admin", llm_configs={}, engine_version="x")
    store.save(record, rating="up", note=None)

    row = store.list()[0]
    assert row["datasource_ids"] == ["chinook"] and row["source"] == "generated"
    store.close()


OLD_SCHEMA = """
CREATE TABLE feedback (
    trace_id TEXT PRIMARY KEY, created_at INTEGER NOT NULL,
    rating TEXT NOT NULL CHECK (rating IN ('up', 'down')), note TEXT, question TEXT NOT NULL,
    role TEXT NOT NULL, status TEXT NOT NULL, sql TEXT NOT NULL, error_codes TEXT NOT NULL,
    retries INTEGER NOT NULL DEFAULT 0, validator_failures INTEGER NOT NULL DEFAULT 0,
    plan_cache_hits INTEGER NOT NULL DEFAULT 0, sub_queries INTEGER NOT NULL DEFAULT 0,
    models TEXT NOT NULL, engine_version TEXT NOT NULL
);
"""


def test_a_store_from_before_the_new_columns_gains_them_and_keeps_its_rows(tmp_path):
    import sqlite3

    path = tmp_path / "schema_store.db"
    with sqlite3.connect(path) as conn:
        conn.execute(OLD_SCHEMA)
        conn.execute("INSERT INTO feedback VALUES ('old', 1, 'up', NULL, 'How many?', 'admin', 'success', "
                     "'[\"SELECT 1\"]', '[]', 0, 0, 0, 1, '{}', 'x')")

    store = FeedbackStore(path)
    columns = {r[1] for r in store._connection.execute("PRAGMA table_info(feedback)")}
    assert {"datasource_ids", "source"} <= columns
    old = store.list()[0]
    assert old["question"] == "How many?" and old["datasource_ids"] is None and old["source"] is None

    store.save(run_record(_result(sub_queries=[{"sql": "SELECT 2", "datasource_id": "chinook"}]),
                          question="q", role="admin", llm_configs={}, engine_version="x"),
               rating="up", note=None)
    assert len(store.list()) == 2
    store.close()
    FeedbackStore(path).close()  # opening a migrated store again changes nothing
