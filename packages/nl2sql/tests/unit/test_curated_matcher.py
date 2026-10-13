"""The curated matcher node: each way it answers, and each reason it falls back.

A real SQLite database stands in for Chinook (three tables, a few rows), the
model is ``FakeLLMServer`` answering ``VerifiedQueryChoice``, and the vector
search is a stub returning the candidates a test names. A fallback leaves the
state untouched apart from the matcher's own record, so the run continues at
the datasource resolver as if the matcher were not there.
"""
from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from nl2sql.adapters.sqlite.adapter import SqliteAdapter
from nl2sql.auth import UserContext
from nl2sql.auth.models import RolePolicy
from nl2sql.auth.rbac import RBAC
from nl2sql.common.settings import settings
from nl2sql.configs.curated import CuratedFileConfig
from nl2sql.indexing.models import VerifiedQueryCandidate
from nl2sql.llm import LLMRegistry
from nl2sql.llm.models import AgentConfig
from nl2sql.pipeline.nodes.curated_matcher import CuratedMatcherNode
from nl2sql.pipeline.state import GraphState
from nl2sql.secrets import SecretManager
from nl2sql.testing.fake_llm import FakeLLMServer, Rule
from nl2sql_adapter_sdk.capabilities import DatasourceCapability

FAKE_KEY = "-".join(["fake", "key", "for", "matcher"])

TOP_SQL = """
SELECT c.CustomerId, c.FirstName AS customer, SUM(il.UnitPrice * il.Quantity) AS revenue
FROM Customer c
JOIN Invoice i ON i.CustomerId = c.CustomerId
JOIN InvoiceLine il ON il.InvoiceId = i.InvoiceId
WHERE CAST(strftime('%Y', i.InvoiceDate) AS INTEGER) = :year
GROUP BY c.CustomerId, customer
ORDER BY revenue DESC, c.CustomerId
LIMIT :n
"""

CURATED = {
    "datasources": {
        "shop": {
            "verified_queries": [
                {
                    "id": "top_customers_by_revenue",
                    "description": "Customers ranked by invoiced revenue in a calendar year, before refunds",
                    "questions": ["Who are our top {n} customers by revenue in {year}?"],
                    "params": {
                        "n": {"type": "int", "default": 10, "min": 1, "max": 100},
                        "year": {"type": "int", "min": 2000, "max": 2100, "example": 2012},
                    },
                    "sql": TOP_SQL,
                },
                {
                    "id": "customer_count",
                    "description": "How many customers there are",
                    "questions": ["How many customers do we have?"],
                    "sql": "SELECT COUNT(*) AS customers FROM Customer",
                },
            ]
        }
    }
}

POLICIES = {
    "analyst": RolePolicy(description="all", role="analyst", allowed_datasources=["shop"],
                          allowed_tables=["shop.Customer", "shop.Invoice", "shop.InvoiceLine"]),
    "viewer": RolePolicy(description="no sales", role="viewer", allowed_datasources=["shop"],
                         allowed_tables=["shop.Customer"]),
    "outsider": RolePolicy(description="other data", role="outsider", allowed_datasources=["other"],
                           allowed_tables=["other.*"]),
}

TOP = VerifiedQueryCandidate(datasource_id="shop", query_id="top_customers_by_revenue", score=0.82,
                             text="Who are our top 10 customers by revenue in 2012?")
COUNT = VerifiedQueryCandidate(datasource_id="shop", query_id="customer_count", score=0.61,
                               text="How many customers do we have?")

QUESTION = "Who were our top 2 customers in 2012?"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "shop.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE Customer (CustomerId INTEGER PRIMARY KEY, FirstName TEXT);
        CREATE TABLE Invoice (InvoiceId INTEGER PRIMARY KEY, CustomerId INTEGER, InvoiceDate TEXT);
        CREATE TABLE InvoiceLine (InvoiceLineId INTEGER PRIMARY KEY, InvoiceId INTEGER, UnitPrice REAL, Quantity INTEGER);
        INSERT INTO Customer VALUES (1, 'Ana'), (2, 'Bo'), (3, 'Cy');
        INSERT INTO Invoice VALUES (10, 1, '2012-02-01'), (11, 2, '2012-05-01'), (12, 3, '2012-07-01'), (13, 1, '2011-01-01');
        INSERT INTO InvoiceLine VALUES (100, 10, 1.0, 3), (101, 11, 2.0, 4), (102, 12, 1.0, 1), (103, 13, 9.0, 9);
    """)
    conn.commit()
    conn.close()
    return path


@pytest.fixture(autouse=True)
def _artifacts(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "result_artifact_backend", "local")
    monkeypatch.setattr(settings, "result_artifact_base_uri", str(tmp_path / "artifacts"))
    monkeypatch.setattr(settings, "result_artifact_path_template", "<tenant_id>/<request_id>/<sub_query_id>.parquet")


class _Registry:
    def __init__(self, adapter, capabilities=None):
        self.adapter = adapter
        self.capabilities = capabilities if capabilities is not None else {c.value for c in adapter.capabilities()}

    def list_ids(self):
        return ["shop"]

    def get_adapter(self, ds_id):
        assert ds_id == "shop"
        return self.adapter

    def get_dialect(self, ds_id):
        return self.adapter.get_dialect()

    def supports(self, ds_id, *caps):
        return ds_id == "shop" and {getattr(c, "value", c) for c in caps} <= self.capabilities


class _VectorStore:
    def __init__(self, candidates):
        self.candidates = candidates
        self.calls = []

    def retrieve_verified_query_candidates(self, query, datasource_ids, k=None, explain=None):
        self.calls.append({"query": query, "datasource_ids": list(datasource_ids), "k": k})
        if explain is not None:
            explain.append({"kind": "curated", "query": query, "pool": []})
        return [c for c in self.candidates if c.datasource_id in datasource_ids]


def _server(choice):
    rules = [] if choice is None else [Rule("VerifiedQueryChoice", choice)]
    return FakeLLMServer(rules).start()


def _ctx(db, server, candidates, capabilities=None, curated=None):
    registry = LLMRegistry(SecretManager())
    registry.register_llm(AgentConfig(provider="openai", model="gpt-4o", api_key=FAKE_KEY,
                                      base_url=server.base_url, name="default"))
    adapter = SqliteAdapter(datasource_id="shop", datasource_engine_type="sqlite",
                            connection_args={"type": "sqlite", "database": str(db)})
    vector_store = _VectorStore(candidates)
    ctx = SimpleNamespace(
        curated=CuratedFileConfig.model_validate(curated or CURATED),
        vector_store=vector_store,
        rbac=RBAC(POLICIES),
        ds_registry=_Registry(adapter, capabilities),
        schema_store=SimpleNamespace(get_latest_version=lambda _id: "v1"),
        tenant_id="t1",
        llm_registry=registry,
    )
    return ctx


def _matcher(db, server, candidates, capabilities=None, curated=None):
    ctx = _ctx(db, server, candidates, capabilities, curated)
    return CuratedMatcherNode(ctx), ctx.vector_store


def _run(db, choice, candidates=(TOP, COUNT), role="analyst", question=QUESTION, capabilities=None,
         datasource_id=None, curated=None):
    server = _server(choice)
    try:
        node, vector_store = _matcher(db, server, list(candidates), capabilities, curated)
        state = GraphState(user_query=question, user_context=UserContext(roles=[role]),
                           datasource_id=datasource_id)
        out = node(state)
    finally:
        server.stop()
    return out, server, vector_store


def _fell_back(out, reason):
    """A fallback: no state the rest of the graph reads, and the reason on the record."""
    assert out["curated_match"]["matched"] is False
    assert out["curated_match"]["reason"] == reason, out["curated_match"]
    for key in ("verified_match", "decomposer_response", "execution_dag", "artifact_refs",
                "subgraph_outputs", "errors"):
        assert not out.get(key), key


CHOOSE_TOP = {"query_id": "shop.top_customers_by_revenue",
              "params": [{"name": "n", "value": "2"}, {"name": "year", "value": "2012"}],
              "reason": "Top customers by revenue in a year."}


# -- a match ------------------------------------------------------------------

def test_a_match_runs_the_verified_sql_with_bound_params_and_fills_the_state(db):
    out, server, _ = _run(db, CHOOSE_TOP)

    match = out["verified_match"]
    assert (match.datasource_id, match.query_id) == ("shop", "top_customers_by_revenue")
    assert match.params == {"n": 2, "year": 2012}
    assert match.description.startswith("Customers ranked by invoiced revenue")

    [sub_query] = out["decomposer_response"].sub_queries
    assert sub_query.datasource_id == "shop" and sub_query.intent == QUESTION
    assert sub_query.schema_version == "v1"
    assert [n.node_id for n in out["execution_dag"].nodes if n.kind == "scan"] == [sub_query.id]

    artifact = out["artifact_refs"][sub_query.id]
    assert artifact.row_count == 2
    [output] = out["subgraph_outputs"].values()
    assert output.plan_source == "verified"
    assert output.status == "success"
    assert ":year" in output.sql_draft and "2012" not in output.sql_draft  # never interpolated
    assert output.row_cap and output.row_cap <= int(settings.result_row_limit)
    assert output.truncated is False
    assert {c.name for c in output.validation} == {"read_only", "params", "policy"}
    assert all(c.passed for c in output.validation)

    # One model call, and it never saw the SQL.
    assert [c["name"] for c in server.calls] == ["VerifiedQueryChoice"]
    prompt = json.dumps(server.calls[0]["body"]["messages"])
    assert "shop.top_customers_by_revenue" in prompt and "shop.customer_count" in prompt
    assert "none_of_these" in prompt
    assert "strftime" not in prompt and "InvoiceLine" not in prompt
    assert "User Query:" in prompt

    record = out["curated_match"]
    assert record["matched"] is True and record["reason"] is None
    assert record["floor"] == settings.curated_match_floor
    assert [c["query_id"] for c in record["candidates"]] == ["top_customers_by_revenue", "customer_count"]
    assert record["choice"]["query_id"] == "shop.top_customers_by_revenue"
    assert record["params"] == {"n": 2, "year": 2012}


def test_the_rows_are_the_verified_querys_rows(db, tmp_path):
    from nl2sql.execution.artifacts import build_artifact_store

    out, _, _ = _run(db, CHOOSE_TOP)

    [artifact] = out["artifact_refs"].values()
    frame = build_artifact_store().read_parquet(artifact)
    assert frame["customer"].to_list() == ["Bo", "Ana"]
    assert frame["revenue"].to_list() == [8.0, 3.0]


def test_a_query_without_params_matches_too(db):
    out, _, _ = _run(db, {"query_id": "shop.customer_count", "params": [], "reason": "count"},
                     question="How many customers are there?")

    assert out["verified_match"].query_id == "customer_count"
    [artifact] = out["artifact_refs"].values()
    assert artifact.row_count == 1


def test_the_row_ceiling_is_enforced_by_the_executor_not_the_sql(db, monkeypatch):
    monkeypatch.setattr(settings, "result_row_limit", 1)

    out, _, _ = _run(db, CHOOSE_TOP)

    [output] = out["subgraph_outputs"].values()
    assert output.artifact.row_count == 1
    assert output.row_cap == 1 and output.truncated is True
    assert "LIMIT :n" in output.sql_draft


def test_only_the_pinned_datasource_is_searched(db):
    _out, _, vector_store = _run(db, CHOOSE_TOP, datasource_id="shop")

    assert vector_store.calls[0]["datasource_ids"] == ["shop"]
    assert vector_store.calls[0]["k"] == settings.curated_match_top_k


# -- each fallback --------------------------------------------------------------

def test_not_configured_for_any_datasource_this_role_may_read(db):
    out, server, vector_store = _run(db, CHOOSE_TOP, role="outsider")

    _fell_back(out, "not_configured")
    assert vector_store.calls == [] and server.calls == []


def test_no_candidates(db):
    out, server, _ = _run(db, CHOOSE_TOP, candidates=())

    _fell_back(out, "no_candidates")
    assert server.calls == []


def test_a_candidate_no_longer_in_the_file_is_skipped(db):
    stale = VerifiedQueryCandidate(datasource_id="shop", query_id="deleted_query", score=0.99, text="x")

    out, server, _ = _run(db, CHOOSE_TOP, candidates=(stale,))

    _fell_back(out, "no_candidates")
    assert server.calls == []


def test_below_the_floor_no_model_call(db):
    low = TOP.model_copy(update={"score": settings.curated_match_floor - 0.01})

    out, server, _ = _run(db, CHOOSE_TOP, candidates=(low,))

    _fell_back(out, "below_floor")
    assert server.calls == []


def test_none_of_these(db):
    out, server, _ = _run(db, {"query_id": "none_of_these", "params": [], "reason": "ranks by tracks"})

    _fell_back(out, "none_of_these")
    assert out["curated_match"]["choice"]["reason"] == "ranks by tracks"
    assert len(server.calls) == 1


def test_model_failed_when_nothing_answers_the_call(db):
    """A keyless demo replay with no recording for the matcher: a clean fallback, never a crash."""
    out, server, _ = _run(db, None)

    _fell_back(out, "model_failed")
    assert [c["name"] for c in server.calls] == ["VerifiedQueryChoice"]


def test_unknown_query(db):
    out, _, _ = _run(db, {"query_id": "shop.made_up", "params": [], "reason": ""})

    _fell_back(out, "unknown_query")


def test_a_real_query_that_was_not_a_candidate_is_unknown(db):
    out, _, _ = _run(db, {"query_id": "shop.customer_count", "params": [], "reason": ""}, candidates=(TOP,))

    _fell_back(out, "unknown_query")


def test_param_invalid(db):
    choice = {**CHOOSE_TOP, "params": [{"name": "n", "value": "500"}, {"name": "year", "value": "2012"}]}

    out, _, _ = _run(db, choice)

    _fell_back(out, "param_invalid")


def test_param_missing(db):
    choice = {**CHOOSE_TOP, "params": [{"name": "n", "value": "5"}]}

    out, _, _ = _run(db, choice)

    _fell_back(out, "param_missing")


def test_policy_denied(db):
    out, _, _ = _run(db, CHOOSE_TOP, role="viewer")

    _fell_back(out, "policy_denied")
    # The record names what was refused, for the data team; nothing else does.
    assert "shop.Invoice" in json.dumps(out["curated_match"])


def test_capability_missing(db):
    out, _, _ = _run(db, CHOOSE_TOP, capabilities={DatasourceCapability.SUPPORTS_SQL.value})

    _fell_back(out, "capability_missing")


def test_execution_failed(db):
    broken = json.loads(json.dumps(CURATED))
    broken["datasources"]["shop"]["verified_queries"][1]["sql"] = "SELECT COUNT(*) FROM NoSuchTable"
    analyst = POLICIES["analyst"].model_copy(update={"allowed_tables": ["shop.*"]})
    POLICIES["analyst_all"] = analyst
    try:
        out, _, _ = _run(db, {"query_id": "shop.customer_count", "params": [], "reason": ""},
                         role="analyst_all", curated=broken)
    finally:
        POLICIES.pop("analyst_all")

    _fell_back(out, "execution_failed")
    # Execution failing is worth a warning; it is not an error of the run.
    assert out["warnings"] and "customer_count" in out["warnings"][0]["message"]


# -- in the graph ---------------------------------------------------------------

def _graph(monkeypatch, ctx, execute=True):
    """The real control graph, the model nodes stubbed; returns it and the nodes that ran."""
    from nl2sql.pipeline import graph as control
    from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AnswerSynthesizerResponse

    ran = []

    def stub(name, result=None):
        def build(_ctx):
            def node(state):
                ran.append(name)
                return result(state) if result else {}
            return node
        return build

    def answer(state):
        return {"answer_synthesizer_response": AnswerSynthesizerResponse(final_answer={
            "summary": "ok", "verified": state.verified_match.query_id if state.verified_match else None})}

    monkeypatch.setattr(control, "DatasourceResolverNode", stub("datasource_resolver"))
    monkeypatch.setattr(control, "DecomposerNode", stub("decomposer"))
    monkeypatch.setattr(control, "AnswerSynthesizerNode", stub("answer_synthesizer", answer))
    from nl2sql.pipeline.subgraphs import sql_agent
    for name in ("SchemaRetrieverNode", "ASTPlannerNode", "LogicalValidatorNode",
                 "GeneratorNode", "ExecutorNode", "RefinerNode"):
        monkeypatch.setattr(sql_agent, name, stub(name))
    return control.build_graph(ctx, execute=execute), ran


def _invoke(graph, question=QUESTION, role="analyst"):
    return graph.invoke(GraphState(user_query=question, user_context=UserContext(roles=[role])).model_dump())


def test_without_curated_answers_the_graph_is_todays(db, monkeypatch):
    server = _server(CHOOSE_TOP)
    try:
        for curated in (None, CuratedFileConfig()):
            ctx = _ctx(db, server, [TOP])
            ctx.curated = curated
            graph, ran = _graph(monkeypatch, ctx)
            assert "curated_matcher" not in graph.nodes
            _invoke(graph)
            assert ran[0] == "datasource_resolver"
        assert server.calls == []
    finally:
        server.stop()


def test_a_plan_only_run_skips_the_matcher(db, monkeypatch):
    server = _server(CHOOSE_TOP)
    try:
        graph, _ = _graph(monkeypatch, _ctx(db, server, [TOP]), execute=False)
    finally:
        server.stop()
    assert "curated_matcher" not in graph.nodes


def test_a_hit_goes_straight_to_the_aggregator_and_the_answer(db, monkeypatch):
    from nl2sql.api.query_api import result_from_state

    server = _server(CHOOSE_TOP)
    try:
        graph, ran = _graph(monkeypatch, _ctx(db, server, [TOP, COUNT]))
        state = _invoke(graph)
    finally:
        server.stop()

    # No resolver, no decomposer, no SQL agent: the matcher, then the shared tail.
    assert ran == ["answer_synthesizer"]
    assert state["answer_synthesizer_response"].final_answer["verified"] == "top_customers_by_revenue"
    [summary] = state["aggregator_response"].results
    assert summary.row_count == 2 and summary.truncated is False

    result = result_from_state(state)
    assert result.source.model_dump() == {"type": "verified", "id": "top_customers_by_revenue",
                                          "datasource_id": "shop", "params": {"n": 2, "year": 2012}}
    [sub_query] = result.sub_queries
    assert sub_query.plan_source == "verified" and sub_query.plan is None
    assert result.status == "success"
    assert result.usage.plan_cache_hits == 0


def test_a_fallback_continues_at_the_resolver_with_nothing_changed(db, monkeypatch):
    from nl2sql.api.query_api import result_from_state

    server = _server({"query_id": "none_of_these", "params": [], "reason": "no"})
    try:
        graph, ran = _graph(monkeypatch, _ctx(db, server, [TOP]))
        state = _invoke(graph)
    finally:
        server.stop()

    assert ran == ["datasource_resolver"]
    assert state.get("verified_match") is None and not state.get("subgraph_outputs")
    assert result_from_state(state).source.model_dump() == {"type": "generated", "id": None,
                                                            "datasource_id": None, "params": None}
