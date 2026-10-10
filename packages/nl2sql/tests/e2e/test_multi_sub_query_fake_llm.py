"""Two sub-queries combined by a join, end to end, through the CLI and the fake LLM.

Every sub-query of a run used to write its result to the same Parquet file,
``<tenant_id>/<request_id>.parquet``. Run side by side, the two writes produced
one corrupt file and the aggregator failed with "File out of specification";
run one after the other, the second overwrote the first and the join combined
a frame with itself -- a wrong answer with no error at all.

The expected answer is a direct SQLite query over both demo databases::

    ATTACH 'chinook.sqlite' AS ch;
    SELECT s.CustomerId FROM (SELECT CustomerId, SUM(Total) spend FROM ch.Invoice
                              GROUP BY CustomerId) s
    JOIN (SELECT DISTINCT customer_id FROM tickets WHERE status = 'open') t
      ON t.customer_id = s.CustomerId
    ORDER BY s.spend DESC LIMIT 5;   -- 45, 37, 7, 25, 5 (7 and 25 tie on 42.62)
"""
from __future__ import annotations

import json
import re

import pytest

from nl2sql.testing.fake_llm import Rule

from .conftest import _base_env, run_cli
from .test_trace_fake_llm import _serve

QUESTION = "Which customers with an open support ticket spent the most on music?"


def _column(alias, name):
    return {"kind": "column", "alias": alias, "column_name": name}


def _sum(alias, name):
    return {"kind": "func", "func_name": "SUM", "is_aggregate": True, "args": [_column(alias, name)]}


def _count(alias, name):
    return {"kind": "func", "func_name": "COUNT", "is_aggregate": True, "args": [_column(alias, name)]}


MUSIC_SPEND_INTENT = "Total music spend per customer"
OPEN_TICKETS_INTENT = "Customers with an open support ticket"
INVOICE_COUNT_INTENT = "Number of invoices per customer"

MUSIC_SPEND_SQ = {
    "id": "sq_music", "datasource_id": "chinook", "intent": MUSIC_SPEND_INTENT,
    "metrics": [{"name": "music_spend", "aggregation": "sum"}],
    "filters": [], "group_by": [{"attribute": "customer"}],
    "expected_schema": [{"name": "customer_id", "dtype": "int"}, {"name": "music_spend", "dtype": "float"}],
}
OPEN_TICKETS_SQ = {
    "id": "sq_tickets", "datasource_id": "support", "intent": OPEN_TICKETS_INTENT,
    "metrics": [{"name": "open_tickets", "aggregation": "count"}],
    "filters": [{"attribute": "status", "operator": "=", "value": "open"}],
    "group_by": [{"attribute": "customer"}],
    "expected_schema": [{"name": "customer_id", "dtype": "int"}, {"name": "open_tickets", "dtype": "int"}],
}
INVOICE_COUNT_SQ = {
    "id": "sq_invoices", "datasource_id": "chinook", "intent": INVOICE_COUNT_INTENT,
    "metrics": [{"name": "invoice_count", "aggregation": "count"}],
    "filters": [], "group_by": [{"attribute": "customer"}],
    "expected_schema": [{"name": "customer_id", "dtype": "int"}, {"name": "invoice_count", "dtype": "int"}],
}


def _join_decomposition(left, right):
    return {
        "sub_queries": [left, right],
        "combine_groups": [{
            "group_id": "g1", "operation": "join",
            "inputs": [{"subquery_id": left["id"], "role": "left"},
                       {"subquery_id": right["id"], "role": "right"}],
            "join_keys": [{"left": "customer_id", "right": "customer_id"}],
        }],
        "post_combine_ops": [{
            "op_id": "op1", "target_group_id": "g1", "operation": "sort",
            "order_by": [{"attribute": "music_spend", "direction": "desc"}], "limit": 5,
        }],
        "unmapped_subqueries": [],
    }


MUSIC_SPEND_PLAN = {
    "query_type": "READ", "distinct": False,
    "tables": [{"name": "Invoice", "alias": "t1"}], "joins": [],
    "select_items": [{"alias": "customer_id", "expr": _column("t1", "CustomerId")},
                     {"alias": "music_spend", "expr": _sum("t1", "Total")}],
    "group_by": [{"expr": _column("t1", "CustomerId")}], "order_by": [],
    "reasoning": "Sum invoice totals per customer.",
}
OPEN_TICKETS_PLAN = {
    "query_type": "READ", "distinct": False,
    "tables": [{"name": "tickets", "alias": "t1"}], "joins": [],
    "select_items": [{"alias": "customer_id", "expr": _column("t1", "customer_id")},
                     {"alias": "open_tickets", "expr": _count("t1", "ticket_id")}],
    "where": {"kind": "binary", "op": "=", "left": _column("t1", "status"),
              "right": {"kind": "literal", "value": "open"}},
    "group_by": [{"expr": _column("t1", "customer_id")}], "order_by": [],
    "reasoning": "Count open tickets per customer.",
}
INVOICE_COUNT_PLAN = {
    "query_type": "READ", "distinct": False,
    "tables": [{"name": "Invoice", "alias": "t1"}], "joins": [],
    "select_items": [{"alias": "customer_id", "expr": _column("t1", "CustomerId")},
                     {"alias": "invoice_count", "expr": _count("t1", "InvoiceId")}],
    "group_by": [{"expr": _column("t1", "CustomerId")}], "order_by": [],
    "reasoning": "Count invoices per customer.",
}


def _echo_customers(prompt_text: str) -> dict:
    """Echoes the customer ids of the aggregated rows, in order."""
    ids = re.findall(r'\\?"customer_id\\?":\s*(\d+)', prompt_text)
    text = "customers: " + ",".join(ids)
    return {"summary": text, "format_type": "text", "content": text, "warnings": []}


def _rules(answerable, decomposition, plans):
    return [
        Rule("AnswerabilityResponse", {"answerable_datasource_ids": answerable,
                                       "reason": "Spend is in the music store, tickets in support."}),
        Rule("DecomposerResponse", decomposition),
        *[Rule("PlanModel", plan, when=intent) for intent, plan in plans],
        Rule("AggregatedResponse", _echo_customers),
        Rule("plain", "Keep the same plan."),
    ]


def _env(trace_dir):
    env = _base_env()
    # Built at run time: GitGuardian scans every push.
    env.update({"OPENAI_API_KEY": "sk-" + "fake-multi-sq", "TRACE_MODE": "always", "TRACE_DIR": str(trace_dir),
                "SQL_AGENT_RETRY_BASE_DELAY_SEC": "0", "SQL_AGENT_RETRY_JITTER_SEC": "0"})
    return env


def _run(demo_project, tmp_path, rules, config_name, question=QUESTION):
    server = _serve(demo_project, rules, config_name)
    try:
        r = run_cli(demo_project, _env(tmp_path), "run", "--llm-config", f"configs/{config_name}", question)
    finally:
        server.stop()
    [path] = list(tmp_path.glob("*.json"))
    return server, r, json.loads(path.read_text(encoding="utf-8"))


def _answer_ids(doc):
    summary = (doc["result"].get("final_answer") or {}).get("summary", "")
    match = re.search(r"customers: ([\d,]*)", summary)
    assert match, summary
    return [int(i) for i in match.group(1).split(",") if i]


@pytest.mark.e2e
def test_two_datasources_joined_on_customer_give_the_sqlite_answer(demo_project, tmp_path):
    rules = _rules(["chinook", "support"], _join_decomposition(MUSIC_SPEND_SQ, OPEN_TICKETS_SQ),
                   [(MUSIC_SPEND_INTENT, MUSIC_SPEND_PLAN), (OPEN_TICKETS_INTENT, OPEN_TICKETS_PLAN)])
    server, r, doc = _run(demo_project, tmp_path, rules, "llm.multi-sq-join.yaml")

    assert doc["result"]["errors"] == [], r.stdout + r.stderr
    uris = [ref["uri"] for ref in doc["result"]["artifact_refs"].values()]
    assert len(uris) == 2 and len(set(uris)) == 2, uris
    ids = _answer_ids(doc)
    assert ids[:2] == [45, 37] and set(ids[2:4]) == {7, 25} and ids[4] == 5, ids


@pytest.mark.e2e
def test_two_sub_queries_on_one_datasource_keep_their_own_results(demo_project, tmp_path):
    # Customer 6 spent the most (49.62) and customer 26 next (47.62). Joined
    # with a frame of itself, music_spend would still sort the same; the
    # invoice count is what proves the right-hand side is the invoice query.
    decomposition = _join_decomposition(MUSIC_SPEND_SQ, INVOICE_COUNT_SQ)
    rules = _rules(["chinook"], decomposition,
                   [(MUSIC_SPEND_INTENT, MUSIC_SPEND_PLAN), (INVOICE_COUNT_INTENT, INVOICE_COUNT_PLAN)])
    server, r, doc = _run(demo_project, tmp_path, rules, "llm.multi-sq-same-ds.yaml",
                          question="How many invoices do the customers who spent the most have?")

    assert doc["result"]["errors"] == [], r.stdout + r.stderr
    uris = [ref["uri"] for ref in doc["result"]["artifact_refs"].values()]
    assert len(uris) == 2 and len(set(uris)) == 2, uris
    assert _answer_ids(doc)[:2] == [6, 26]
    aggregator = [n for n in doc["nodes"] if n["node"] == "aggregator"]
    rows = json.dumps(aggregator)
    assert "invoice_count" in rows and "music_spend" in rows, rows[:2000]
