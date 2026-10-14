"""Curated answers end to end: the design's worked examples (a) to (d) on Chinook.

Through the real CLI and the fake LLM, against the generated demo project. The
module indexes one verified query into a private copy of the demo's index and
state store (``nl2sql index --curated-only`` on a config whose ``storage.path``
is that copy), so no other test sees it. The model's choice is a ``FakeLLMServer`` rule keyed on
``VerifiedQueryChoice``; everything after it is the real engine.

A run whose config has no ``curated`` section has no curated matcher in its
graph at all: the same nodes and the same answer as before curated answers
existed.
"""
from __future__ import annotations

import json

import pytest
import yaml

from .conftest import _base_env, private_storage, run_cli
from .recordings_chinook import RULES_COUNT_CUSTOMERS, RULES_TOP_CUSTOMERS, count_customers_answer
from .test_trace_fake_llm import _serve
from nl2sql.testing.fake_llm import Rule

TOP_SQL = """SELECT c.CustomerId,
       c.FirstName || ' ' || c.LastName AS customer,
       c.Country,
       ROUND(SUM(il.UnitPrice * il.Quantity), 2) AS revenue
FROM Customer c
JOIN Invoice i      ON i.CustomerId = c.CustomerId
JOIN InvoiceLine il ON il.InvoiceId = i.InvoiceId
WHERE CAST(strftime('%Y', i.InvoiceDate) AS INTEGER) = :year
GROUP BY c.CustomerId, customer, c.Country
ORDER BY revenue DESC, c.CustomerId
LIMIT :n
"""

CURATED = {
    "datasources": {
        "chinook": {
            "verified_queries": [{
                "id": "top_customers_by_revenue",
                "description": "Customers ranked by invoiced revenue in a calendar year",
                "questions": ["Who are our top {n} customers by revenue in {year}?", "Best {n} customers in {year}"],
                "params": {
                    "n": {"type": "int", "default": 10, "min": 1, "max": 100},
                    "year": {"type": "int", "min": 2000, "max": 2100, "example": 2012},
                },
                "sql": TOP_SQL,
            }]
        }
    }
}

MATCH_QUESTION = "Who were our top 5 customers in 2012?"


def _choice(**params):
    return {"query_id": "chinook.top_customers_by_revenue",
            "params": [{"name": k, "value": v} for k, v in params.items()],
            "reason": "Top customers by revenue in a year."}


def _verified_answer(prompt_text: str) -> dict:
    # The answer writer is told what the numbers mean: the query's description.
    meaning = "invoiced revenue" if "Customers ranked by invoiced revenue" in prompt_text else "unknown"
    return {"summary": f"Top customers by {meaning}.", "format_type": "text",
            "content": f"Top customers by {meaning}.", "warnings": []}


def _env():
    env = _base_env()
    env["OPENAI_API_KEY"] = "sk-" + "fake-curated"
    return env


def _with_storage(path, store):
    """Point the config at ``path`` at the module's own store."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["storage"] = {"path": str(store)}
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")


@pytest.fixture(scope="module")
def curated(demo_project, tmp_path_factory):
    """A private index and state store holding the verified query."""
    store = private_storage(demo_project, tmp_path_factory.mktemp("curated") / "store")
    server = _serve(demo_project, RULES_COUNT_CUSTOMERS, "curated-index.yaml", curated=CURATED)
    server.stop()
    _with_storage(demo_project / "curated-index.yaml", store)
    r = run_cli(demo_project, _env(), "index", "--curated-only", "--datasource", "chinook",
                config="curated-index.yaml", timeout=600)
    assert r.returncode == 0, r.stdout + r.stderr
    return {"store": store}


def _ask(demo_project, tmp_path, curated, name, rules, question, role="analyst", with_section=True):
    """One CLI run; returns (the fake server, the trace document, the CLI result)."""
    config = f"curated-{tmp_path.name}-{name}.yaml"
    server = _serve(demo_project, rules, config, mode="always", curated=CURATED if with_section else False)
    _with_storage(demo_project / config, curated["store"])
    trace_dir = curated["store"] / "traces"
    for old in trace_dir.glob("*.json"):
        old.unlink()
    try:
        r = run_cli(demo_project, _env(), "run", "--role", role, question, config=config)
    finally:
        server.stop()
    paths = list(trace_dir.glob("*.json"))
    assert len(paths) == 1, r.stdout + r.stderr
    return server, json.loads(paths[0].read_text(encoding="utf-8")), r


def _calls(server):
    return [c["name"] for c in server.calls]


def _nodes(doc):
    return [n["node"] for n in doc["nodes"]]


def _matcher_record(doc):
    [node] = [n for n in doc["nodes"] if n["node"] == "curated_matcher"]
    return node["outputs"]["curated_match"]


@pytest.mark.e2e
def test_a_a_match_is_answered_by_the_verified_query(demo_project, tmp_path, curated):
    rules = [Rule("VerifiedQueryChoice", _choice(n="5", year="2012")),
             Rule("AggregatedResponse", _verified_answer)]

    server, doc, r = _ask(demo_project, tmp_path, curated, "a", rules, MATCH_QUESTION)

    assert r.returncode == 0, r.stdout + r.stderr
    # Two model calls: the match and the reply. No resolver, decomposer or planner.
    assert _calls(server) == ["VerifiedQueryChoice", "AggregatedResponse"]
    assert "datasource_resolver" not in _nodes(doc) and "ast_planner" not in _nodes(doc)

    result = doc["result"]
    assert result["source"] == {"type": "verified", "id": "top_customers_by_revenue",
                                "datasource_id": "chinook", "params": {"n": 5, "year": 2012}}
    [sub] = result["sub_queries"]
    assert sub["plan_source"] == "verified" and sub["status"] == "success"
    assert ":year" in sub["sql"]
    names = [row[1] for row in sub["rows"]["rows"]]
    assert names == ["Richard Cunningham", "João Fernandes", "Fernanda Ramos", "Joakim Johansson", "Mark Taylor"]
    assert [row[3] for row in sub["rows"]["rows"]] == [25.84, 24.77, 24.75, 24.75, 22.77]
    assert result["final_answer"]["summary"] == "Top customers by invoiced revenue."
    assert result["result_manifest"][0]["row_count"] == 5

    record = _matcher_record(doc)
    assert record["matched"] is True
    assert record["candidates"][0]["query_id"] == "top_customers_by_revenue"
    assert record["candidates"][0]["similarity"] >= record["floor"]


@pytest.mark.e2e
def test_b_a_near_miss_is_generated(demo_project, tmp_path, curated):
    rules = [Rule("VerifiedQueryChoice", {"query_id": "none_of_these", "params": [],
                                          "reason": "That query ranks by revenue, not by tracks."}),
             *RULES_TOP_CUSTOMERS]

    server, doc, _r = _ask(demo_project, tmp_path, curated, "b", rules,
                           "Top 5 customers by number of tracks bought in 2012")

    assert _calls(server)[:2] == ["VerifiedQueryChoice", "AnswerabilityResponse"]
    assert "PlanModel" in _calls(server)
    assert doc["result"]["source"]["type"] == "generated"
    assert _matcher_record(doc)["reason"] == "none_of_these"
    assert [sq["plan_source"] for sq in doc["result"]["sub_queries"]] == ["llm"]


@pytest.mark.e2e
@pytest.mark.parametrize("question,params,reason", [
    ("Who are our top customers by revenue?", {"n": "10"}, "param_missing"),
    ("Top 500 customers in 2012", {"n": "500", "year": "2012"}, "param_invalid"),
], ids=["missing-year", "n-above-max"])
def test_c_a_missing_or_out_of_range_value_is_generated(demo_project, tmp_path, curated, question, params, reason):
    rules = [Rule("VerifiedQueryChoice", _choice(**params)), *RULES_TOP_CUSTOMERS]

    server, doc, _r = _ask(demo_project, tmp_path, curated, reason, rules, question)

    assert _matcher_record(doc)["reason"] == reason
    assert doc["result"]["source"]["type"] == "generated"
    assert "DecomposerResponse" in _calls(server)


@pytest.mark.e2e
def test_d_no_access_falls_back_and_the_usual_refusal_follows(demo_project, tmp_path, curated):
    rules = [Rule("VerifiedQueryChoice", _choice(n="5", year="2012")), *RULES_TOP_CUSTOMERS]

    server, doc, r = _ask(demo_project, tmp_path, curated, "d", rules, MATCH_QUESTION, role="viewer")

    record = _matcher_record(doc)
    assert record["reason"] == "policy_denied"
    result = doc["result"]
    assert result["source"]["type"] == "generated"
    codes = [e["error_code"] for e in result["errors"]]
    assert "SECURITY_VIOLATION" in codes, r.stdout + r.stderr
    # The user is not told a verified query exists that they may not use.
    assert "top_customers_by_revenue" not in json.dumps(result["errors"])


@pytest.mark.e2e
def test_a_replay_with_no_recording_for_the_matcher_falls_back_and_still_answers(demo_project, tmp_path, curated):
    """The keyless demo replays recordings; until they hold the matcher's call, it misses."""
    server, doc, r = _ask(demo_project, tmp_path, curated, "replay", RULES_COUNT_CUSTOMERS, MATCH_QUESTION)

    assert _matcher_record(doc)["reason"] == "model_failed"
    assert doc["result"]["source"]["type"] == "generated"
    assert doc["result"]["status"] == "success", r.stdout + r.stderr
    assert doc["result"]["errors"] == []


@pytest.mark.e2e
def test_without_a_curated_section_the_graph_and_the_answer_are_todays(demo_project, tmp_path, curated):
    question = "How many customers are there?"
    rules = [Rule("VerifiedQueryChoice", _choice(n="5", year="2012")), *RULES_COUNT_CUSTOMERS]

    server, doc, r = _ask(demo_project, tmp_path, curated, "off", rules, question, with_section=False)

    assert r.returncode == 0, r.stdout + r.stderr
    assert "VerifiedQueryChoice" not in _calls(server)
    assert _calls(server) == ["AnswerabilityResponse", "DecomposerResponse", "PlanModel", "AggregatedResponse"]
    assert _nodes(doc)[0] == "datasource_resolver" and "curated_matcher" not in _nodes(doc)
    result = doc["result"]
    assert result["source"] == {"type": "generated", "id": None, "datasource_id": None, "params": None}
    assert result["final_answer"]["summary"] == count_customers_answer("customer_count 59")["summary"]
