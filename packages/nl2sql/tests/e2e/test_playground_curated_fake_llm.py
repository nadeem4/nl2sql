"""The playground passes a verified answer's ``source`` through, so the page can badge it.

The demo project has a ``curated`` section (``curated.yaml``, written by
``nl2sql init --demo``) and its questions are in the index. A question one of its verified queries answers
comes back from ``/api/ask``, and in the ``final`` event of
``/api/ask/stream``, with ``source.type == "verified"``, the query's id and the
values that were bound, and a sub-query with the verified SQL and no plan. A
question none answers comes back with ``source.type == "generated"``.

The model's choice is a ``FakeLLMServer`` rule keyed on ``VerifiedQueryChoice``;
everything after it is the real engine against the demo's Chinook.
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from nl2sql.testing.fake_llm import Rule  # noqa: E402

from .recordings_chinook import RULES_COUNT_CUSTOMERS  # noqa: E402
from .test_result_paging_fake_llm import _env  # noqa: E402
from .test_stream_fake_llm import parse_sse  # noqa: E402

QUESTION = "Who were our top 5 customers by revenue in 2012?"

CHOICE = {"query_id": "chinook.top_customers_by_revenue",
          "params": [{"name": "n", "value": "5"}, {"name": "year", "value": "2012"}],
          "reason": "Top customers by revenue in a year."}
ANSWER = {"summary": "Richard Cunningham spent the most in 2012.", "format_type": "text",
          "content": "Richard Cunningham spent the most in 2012.", "warnings": []}
NONE = {"query_id": "none_of_these", "params": [], "reason": "Nothing counts customers."}


def _client(demo_project, fake_llm, monkeypatch, rules):
    from fastapi.testclient import TestClient

    from nl2sql import NL2SQL
    from nl2sql.cli.demo.playground.app import build_app

    import yaml

    server, env = fake_llm(rules)
    # The demo's own verified queries.
    path = demo_project / "fake.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["curated"] = {"file": "curated.yaml"}
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    _env(monkeypatch, demo_project, env)
    app = build_app(NL2SQL.from_config(), questions=[], roles=["admin"], mode="live", dataset="chinook")
    return server, TestClient(app)


@pytest.mark.e2e
def test_a_verified_answer_carries_its_source_on_ask_and_on_the_stream(demo_project, fake_llm, monkeypatch):

    rules = [Rule("VerifiedQueryChoice", CHOICE), Rule("AggregatedResponse", ANSWER)]
    server, client = _client(demo_project, fake_llm, monkeypatch, rules)
    asked = client.post("/api/ask", json={"question": QUESTION, "role": "admin"}).json()
    events = parse_sse(client.post("/api/ask/stream", json={"question": QUESTION, "role": "admin"}).text)

    assert asked["status"] == "success", asked["errors"]
    assert asked["source"] == {"type": "verified", "id": "top_customers_by_revenue",
                               "datasource_id": "chinook", "params": {"n": 5, "year": 2012}}
    [sub] = asked["sub_queries"]
    assert sub["plan"] is None and sub["plan_source"] == "verified"
    assert ":year" in sub["sql"] and ":n" in sub["sql"]
    assert sub["rows"]["total_rows"] == 5
    # Only the choice and the answer: no planner, no decomposer.
    assert [c["name"] for c in server.calls][:2] == ["VerifiedQueryChoice", "AggregatedResponse"]

    name, finished = events[-1]
    assert name == "run.finished"
    final = finished["result"]
    assert final["source"] == asked["source"]
    assert final["sub_queries"][0]["plan"] is None


@pytest.mark.e2e
def test_a_generated_answer_says_so(demo_project, fake_llm, monkeypatch):

    rules = [Rule("VerifiedQueryChoice", NONE), *RULES_COUNT_CUSTOMERS]
    _, client = _client(demo_project, fake_llm, monkeypatch, rules)
    asked = client.post("/api/ask", json={"question": "How many customers are there?", "role": "admin"}).json()

    assert asked["status"] == "success", asked["errors"]
    assert asked["source"]["type"] == "generated"
    assert asked["sub_queries"][0]["plan"] is not None
