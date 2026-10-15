"""Routing at scale (wave_a.md, section 9, "The routing gate").

The test bed's PostgreSQL sample, pagila, beside its generated wide schema:
1,200 tables of business nouns (orders, customers, invoices, payments) with
meaningless columns. A question about films goes to pagila and a question
about payroll runs or ledger entries goes to the wide schema, however many
tables there are to vote. Each question is a routing case run in mode
retrieval (no model), so the routing scorer decides it as it does the
routing suite's: the right datasource first, and every one it needs
shortlisted.

The wide schema's index takes minutes to build, so it is built once, on
PostgreSQL; routing reads the index, not the database, so another server
would measure the same thing again.
"""
from __future__ import annotations

import pytest

from nl2sql import NL2SQL
from nl2sql.evaluation.suite import write_suite
from nl2sql.testing.config import make_config

from .servers import datasource

PAGILA = ("A DVD rental store: films with their actors, categories and languages, the inventory in each "
          "store, rentals, payments, customers and staff.")
WIDE = ("Generated enterprise schemas for sales, finance, hr, marketing and ops: ledgers, payroll runs, "
        "campaign clicks, stock levels, vendor contacts, contracts, budgets and shipments.")
CASES = [
    ("Which actors appeared in the most films?", "pagila"),
    ("How many films are in each category?", "pagila"),
    ("What did each customer pay for rentals?", "pagila"),
    ("Which films were rented most often?", "pagila"),
    ("How many payroll runs are there?", "wide"),
    ("What is the total of the ledger entries?", "wide"),
    ("Which vendor contacts are recorded?", "wide"),
    ("How many campaign clicks were there?", "wide"),
]


def test_routing_on_the_wide_schema(server, tmp_path):
    if server.name != "postgres":
        pytest.skip("The wide schema's index is built once, on PostgreSQL (see the module docstring).")
    sources = {"pagila": datasource(server, description=PAGILA), "wide": datasource(server, "wide", description=WIDE)}
    config = make_config(tmp_path / "project", datasources=[
        s.model_dump(mode="json", exclude_defaults=True) for s in sources.values()])
    suite = tmp_path / "routing_wide.yaml"
    write_suite(suite, {"version": 1, "cases": [
        {"id": f"wide_{i:02d}", "question": question, "datasources": [sources[name].id], "status": "approved",
         "outcome": {"admin": "allowed"}, "tags": ["routing", name]}
        for i, (question, name) in enumerate(CASES, 1)]})

    with NL2SQL(config) as engine:
        built = engine.index.build()
        assert built.ok, built.errors
        report = engine.evals.run(suites=[suite], mode="retrieval", scorers=["routing"])

    assert report.database["tables"] >= 1200
    results = report.configs["retrieval"]["results"]
    assert len(results) == len(CASES)
    missed = {r["question"]: r["scores"]["routing"]["detail"] for r in results if r["status"] != "pass"}
    assert not missed, missed
