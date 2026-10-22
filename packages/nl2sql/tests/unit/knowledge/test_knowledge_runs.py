"""Knowledge in a whole run, on a real engine with a fake model (docs/architecture/wave_b.md, sections 4 and 8).

A glossary join lets a plan the database declares no key for validate and
run; the result's ``knowledge_used`` and ``explanation`` hold only what the
caller's scope reaches: a denied table's description never reaches the
explanation, which is rendered from the caller's scope-filtered knowledge.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List

import yaml
from langchain_core.embeddings import DeterministicFakeEmbedding

from nl2sql import NL2SQL, Embeddings, UserContext
from nl2sql.testing.config import make_config
from nl2sql.testing.fake_llm import FakeLLMServer, Rule

FAKE_KEY = "-".join(["sk", "knowledge", "runs", "0000"])

GLOSSARY = {
    "version": 1,
    "datasources": {"shop": {
        "tables": {"orders": {"description": "One row per order."},
                   "customers": {"description": "People who buy. Their pay is never in Salaries."}},
        "joins": [{"from": "orders.customer_id", "to": "customers.id", "cardinality": "many-to-one"}],
        "terms": [{"term": "revenue", "synonyms": ["sales"], "definition": "Sum of orders.total.",
                   "columns": ["orders.total"]},
                  {"term": "payroll", "synonyms": ["sales"], "definition": "Sum of pay.", "tables": ["salaries"]}],
        "rules": [{"id": "pay_private", "text": "Never show salaries per person."},
                  {"id": "no_cancelled", "text": "Cancelled orders do not count.", "tables": ["orders"]}],
    }},
}
ROLES = {"admin": {"description": "Everything", "allow": ["*"]},
         "analyst": {"description": "All but pay", "allow": ["shop"], "deny": ["shop.main.salaries"]}}
ADMIN = UserContext(roles=["admin"])
ANALYST = UserContext(roles=["analyst"])

DECOMPOSITION = {
    "sub_queries": [{"id": "sq1", "datasource_id": "shop", "intent": "sales by customer name",
                     "metrics": [{"name": "revenue", "aggregation": "sum"}], "filters": [],
                     "group_by": [{"attribute": "name"}],
                     "expected_schema": [{"name": "name", "dtype": "string"}, {"name": "revenue", "dtype": "float"}]}],
    "combine_groups": [{"group_id": "g1", "operation": "standalone", "inputs": [{"subquery_id": "sq1"}],
                        "join_keys": []}],
    "post_combine_ops": [], "unmapped_subqueries": [],
}
PLAN = {
    "tables": [{"name": "orders", "alias": "t1"}, {"name": "customers", "alias": "t2"}],
    "joins": [{"left_alias": "t1", "right_alias": "t2", "join_type": "inner", "condition": {
        "kind": "binary", "op": "=", "left": {"kind": "column", "alias": "t1", "column_name": "customer_id"},
        "right": {"kind": "column", "alias": "t2", "column_name": "id"}}}],
    "select_items": [
        {"alias": "name", "expr": {"kind": "column", "alias": "t2", "column_name": "name"}},
        {"alias": "revenue", "expr": {"kind": "func", "func_name": "SUM", "is_aggregate": True,
                                      "args": [{"kind": "column", "alias": "t1", "column_name": "total"}]}}],
    "group_by": [{"expr": {"kind": "column", "alias": "t2", "column_name": "name"}}],
    "reasoning": "Revenue per customer through the glossary join.",
}
RULES = [
    Rule("AnswerabilityResponse", {"answerable_datasource_ids": ["shop"], "reason": "The shop's data."}),
    Rule("DecomposerResponse", DECOMPOSITION),
    Rule("PlannerAnswer", PLAN),
    Rule("AggregatedResponse", {"summary": "Done.", "format_type": "text", "content": "Done.", "warnings": []}),
    Rule("plain", "Keep the same plan."),
]


def _engine(tmp_path: Path, base_url: str, knowledge: bool = True, observers=()) -> NL2SQL:
    tmp_path.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(tmp_path / "shop.db") as connection:
        for statement in (
            "CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT)",
            "CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, total REAL)",
            "CREATE TABLE salaries (id INTEGER PRIMARY KEY, amount REAL)",
            "INSERT INTO customers VALUES (1, 'Ada'), (2, 'Bo')",
            "INSERT INTO orders VALUES (1, 1, 10.0), (2, 1, 5.0), (3, 2, 7.5)",
        ):
            connection.execute(statement)
    connection.close()
    (tmp_path / "glossary.yaml").write_text(yaml.safe_dump(GLOSSARY, sort_keys=False), encoding="utf-8")
    cfg = make_config(tmp_path, datasources=[{"id": "shop", "type": "sqlite", "connection": {"database": "shop.db"}}],
                      policies={"roles": ROLES},
                      llm={"provider": "openai", "model": "gpt-4o", "base_url": base_url, "api_key": FAKE_KEY},
                      knowledge=[{"type": "yaml", "path": "glossary.yaml"}] if knowledge else [])
    engine = NL2SQL(cfg.model_copy(update={"embeddings": Embeddings(instance=DeterministicFakeEmbedding(size=8),
                                                                     dimension=8), "observers": list(observers)}))
    assert engine.index.build().ok
    return engine


def _ask(tmp_path, user, knowledge=True, observers=()):
    server = FakeLLMServer(list(RULES)).start()
    try:
        with _engine(tmp_path, server.base_url, knowledge, observers) as engine:
            result = engine.run_query("Sales by customer?", user_context=user)
    finally:
        server.stop()
    planner: List[str] = ["\n".join(str(m.get("content")) for m in c["body"]["messages"])
                          for c in server.calls if c["name"] == "PlannerAnswer"]
    return result, planner


def test_a_glossary_join_answers_a_question_the_schema_cannot(tmp_path):
    result, planner = _ask(tmp_path / "with", ANALYST)
    without, _ = _ask(tmp_path / "without", ANALYST, knowledge=False)

    assert result.status == "success", result.errors
    assert sorted(result.sub_queries[0].rows.rows) == [["Ada", 15.0], ["Bo", 7.5]]
    assert "[BUSINESS_CONTEXT]" in planner[0] and '"term":"revenue"' in planner[0]
    assert without.status != "success"


def test_knowledge_used_holds_only_items_within_the_callers_scope(tmp_path):
    seen = []

    class Recorder:
        events = ["knowledge.used"]

        def on_event(self, event):
            seen.append(event)

    result, planner = _ask(tmp_path, ANALYST, observers=[Recorder()])

    used = result.sub_queries[0].knowledge_used
    assert [t.term for t in used.terms] == ["revenue"]
    assert [r.id for r in used.rules] == ["no_cancelled"]
    assert "salaries" not in used.model_dump_json().lower()
    assert all("salaries" not in text.lower() for text in planner)
    # knowledge.used is published once, with the same scoped items.
    [event] = seen
    assert event.data["terms"] == {"revenue": 0.9} and event.data["rules"] == ["no_cancelled"]
    assert event.data["digest"] == used.digest


def test_a_denied_tables_description_never_reaches_the_explanation(tmp_path):
    analyst, _ = _ask(tmp_path / "analyst", ANALYST)
    admin, _ = _ask(tmp_path / "admin", ADMIN)

    # The customers description mentions the denied table, so the analyst's explanation names the table bare.
    assert analyst.explanation.steps[0].tables == ["orders: one row per order (glossary)", "customers"]
    assert "salaries" not in json.dumps(analyst.explanation.model_dump(mode="json")).lower()
    # The admin's scope reaches it, so the same description names the table for them.
    assert "customers: people who buy (glossary)" in admin.explanation.steps[0].tables
    assert any("glossary" in join for join in analyst.explanation.steps[0].joins)
