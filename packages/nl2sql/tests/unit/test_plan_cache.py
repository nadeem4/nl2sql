"""The plan cache: validated plans pinned by (question, datasource, schema version).

A hit replaces only the planner's LLM call. The validator, generator and
executor still run on the cached plan, so the cache can never become a way
around the policy. These tests pin the key, the normalisation, the storage in
the schema store, and what the planner node and the subgraph wrapper do with it.
"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nl2sql.common.errors import ErrorCode, ErrorSeverity, PipelineError
from nl2sql.common.settings import settings
from nl2sql.execution.contracts import ArtifactRef, ExecutorResponse
from nl2sql.pipeline.graph_utils import wrap_subgraph
from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse, SubQuery
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.plan_cache import PlanCache, normalize_question
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.schema import InMemorySchemaStore, SqliteSchemaStore


@pytest.fixture(autouse=True)
def _cache_on(monkeypatch):
    monkeypatch.setattr(settings, "plan_cache_enabled", True)


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path):
    if request.param == "sqlite":
        s = SqliteSchemaStore(path=tmp_path / "schema_store.db")
        yield s
        s.close()
    else:
        yield InMemorySchemaStore()


def _plan(reasoning="Count rows in Customer.") -> PlanModel:
    return PlanModel.model_validate({
        "query_type": "READ",
        "tables": [{"name": "Customer", "alias": "t1"}],
        "select_items": [{"alias": "n",
                          "expr": {"kind": "func", "func_name": "COUNT", "is_aggregate": True,
                                   "args": [{"kind": "column", "alias": "t1", "column_name": "CustomerId"}]}}],
        "reasoning": reasoning,
    })


def _sq(intent="How many customers are there?", ds="chinook", version="v1") -> SubQuery:
    return SubQuery(id="sq1", datasource_id=ds, intent=intent, schema_version=version)


# -- normalisation ------------------------------------------------------

@pytest.mark.parametrize("raw", [
    "How many customers are there?",
    "how many customers are there",
    "  HOW   many\tcustomers\nare there ?? ",
    "How many customers are there.",
    "How many customers are there!",
    "How many customers are there ;:,",
])
def test_normalisation_case_folds_collapses_whitespace_and_strips_trailing_punctuation(raw):
    assert normalize_question(raw) == "how many customers are there"


def test_normalisation_keeps_inner_punctuation_and_words():
    # Only the end is stripped: the question's meaning is untouched.
    assert normalize_question("Sales in 2009, by country?") == "sales in 2009, by country"
    assert normalize_question("top 5 artists") != normalize_question("top 10 artists")


def test_normalisation_of_empty_text_is_empty():
    assert normalize_question("") == ""
    assert normalize_question("  ?! ") == ""


# -- the cache over the schema store -------------------------------------

def test_a_stored_plan_comes_back_for_the_same_normalised_question(store):
    cache = PlanCache(store)
    assert cache.get(_sq()) is None

    assert cache.put(_sq(), _plan()) is True

    hit = cache.get(_sq(intent="  how MANY customers are there "))
    assert hit == _plan()


def test_a_changed_schema_version_misses(store):
    cache = PlanCache(store)
    cache.put(_sq(version="v1"), _plan())

    assert cache.get(_sq(version="v2")) is None
    assert cache.get(_sq(version="v1")) == _plan()


def test_another_datasource_misses(store):
    cache = PlanCache(store)
    cache.put(_sq(ds="chinook"), _plan())

    assert cache.get(_sq(ds="sales")) is None


def test_a_different_question_misses(store):
    cache = PlanCache(store)
    cache.put(_sq(), _plan())

    assert cache.get(_sq(intent="How many customers are there in Brazil?")) is None


def test_a_sub_query_without_a_schema_version_is_never_cached(store):
    # Without a version nothing could invalidate the entry when the schema changes.
    cache = PlanCache(store)

    assert cache.put(_sq(version=None), _plan()) is False
    assert cache.get(_sq(version=None)) is None


def test_putting_again_replaces_the_plan(store):
    cache = PlanCache(store)
    cache.put(_sq(), _plan("first"))
    cache.put(_sq(), _plan("second"))

    assert cache.get(_sq()).reasoning == "second"


def test_the_disable_setting_turns_off_reads_and_writes(store, monkeypatch):
    cache = PlanCache(store)
    cache.put(_sq(), _plan())

    monkeypatch.setattr(settings, "plan_cache_enabled", False)
    assert cache.get(_sq()) is None
    assert cache.put(_sq(intent="another question"), _plan()) is False

    monkeypatch.setattr(settings, "plan_cache_enabled", True)
    assert cache.get(_sq(intent="another question")) is None


def test_clear_removes_every_cached_plan(store):
    cache = PlanCache(store)
    cache.put(_sq(), _plan())
    cache.put(_sq(intent="Other question", ds="sales"), _plan())

    assert cache.clear() == 2
    assert cache.get(_sq()) is None
    assert cache.clear() == 0


def test_a_cache_without_a_store_is_a_no_op():
    cache = PlanCache(None)

    assert cache.put(_sq(), _plan()) is False
    assert cache.get(_sq()) is None
    assert cache.clear() == 0


def test_the_sqlite_cache_lives_in_the_schema_store_file_and_survives_a_reopen(tmp_path):
    path = tmp_path / "schema_store.db"
    first = SqliteSchemaStore(path=path)
    PlanCache(first).put(_sq(), _plan())
    first.close()

    reopened = SqliteSchemaStore(path=path)
    try:
        assert PlanCache(reopened).get(_sq()) == _plan()
    finally:
        reopened.close()


def test_an_unreadable_cached_plan_is_a_miss(tmp_path):
    store = SqliteSchemaStore(path=tmp_path / "schema_store.db")
    try:
        store.put_cached_plan("how many customers are there", "chinook", "v1", "{not json")
        assert PlanCache(store).get(_sq()) is None
    finally:
        store.close()


def test_a_plan_cached_before_the_ordinal_fields_were_dropped_is_a_miss(tmp_path):
    """An entry written by an older version is skipped, not raised on.

    ``PlanModel`` forbids extra fields, so a stored plan carrying the
    ``ordinal`` keys no longer deserialises. A store full of them must still
    behave as an empty cache: the planner runs and overwrites the entry.
    """
    stale = json.dumps({
        "query_type": "READ",
        "tables": [{"name": "Customer", "alias": "t1", "ordinal": 0}],
        "select_items": [{"ordinal": 0, "alias": "n",
                          "expr": {"kind": "column", "alias": "t1", "column_name": "CustomerId"}}],
    })
    store = SqliteSchemaStore(path=tmp_path / "schema_store.db")
    try:
        cache = PlanCache(store)
        store.put_cached_plan("how many customers are there", "chinook", "v1", stale)

        assert cache.get(_sq()) is None

        # And the entry is replaced the moment a fresh plan validates.
        assert cache.put(_sq(), _plan()) is True
        assert cache.get(_sq()) == _plan()
    finally:
        store.close()


# -- the planner node -----------------------------------------------------

def _planner(store):
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx = SimpleNamespace(llm_registry=MagicMock(), schema_store=store)
    ctx.llm_registry.get_llm.return_value = llm
    node = ASTPlannerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = _plan("from the model")
    return node


def test_a_hit_skips_the_model_and_says_the_plan_came_from_the_cache(store):
    PlanCache(store).put(_sq(), _plan("cached"))
    node = _planner(store)

    out = node(SubgraphExecutionState(trace_id="t", sub_query=_sq()))

    node.chain.invoke.assert_not_called()
    response = out["ast_planner_response"]
    assert response.plan.reasoning == "cached"
    assert response.plan_source == "cache"


def test_a_miss_calls_the_model_and_says_so(store):
    node = _planner(store)

    out = node(SubgraphExecutionState(trace_id="t", sub_query=_sq()))

    node.chain.invoke.assert_called_once()
    assert out["ast_planner_response"].plan_source == "llm"


def test_the_planner_does_not_store_a_plan_itself(store):
    # Only a plan that passed validation and executed is stored, which the
    # planner cannot know yet.
    node = _planner(store)
    node(SubgraphExecutionState(trace_id="t", sub_query=_sq()))

    assert PlanCache(store).get(_sq()) is None


def test_a_retry_asks_the_model_even_when_a_plan_is_cached(store):
    # The cached plan was rejected on this attempt; serving it again would loop.
    PlanCache(store).put(_sq(), _plan("cached"))
    node = _planner(store)
    rejected = PipelineError(node="logical_validator", message="bad", severity=ErrorSeverity.ERROR,
                             error_code=ErrorCode.TABLE_NOT_FOUND)

    out = node(SubgraphExecutionState(trace_id="t", sub_query=_sq(), retry_count=1, errors=[rejected]))

    node.chain.invoke.assert_called_once()
    assert out["ast_planner_response"].plan_source == "llm"


def test_a_planner_built_without_a_schema_store_still_plans():
    node = _planner(None)
    out = node(SubgraphExecutionState(trace_id="t", sub_query=_sq()))
    assert out["ast_planner_response"].plan_source == "llm"


# -- the subgraph wrapper: what gets stored ---------------------------------

class _Sub:
    def __init__(self, returned):
        self.returned = returned

    def invoke(self, state, config=None):
        return {**state, **self.returned}


def _wrapper_state():
    return {
        "trace_id": "t1",
        "subgraph_id": "sql_agent:sq1:t1",
        "user_context": None,
        "decomposer_response": DecomposerResponse(sub_queries=[_sq()], combine_groups=[]),
    }


def _artifact():
    return ArtifactRef(uri="x", backend="local", format="parquet", row_count=1, columns=["n"], bytes=1,
                       content_hash="h", created_at=datetime.now(), path_template="p")


def _executed(artifact=None, errors=()):
    return ExecutorResponse(executor_name="sqlite", subgraph_name="sql_agent", node_id="sq1", trace_id="t1",
                            tenant_id="default_tenant", artifact=artifact, errors=list(errors))


def _run_wrapped(store, returned, execute=True):
    ctx = SimpleNamespace(schema_store=store)
    return wrap_subgraph(_Sub(returned), "sql_agent", ctx, execute=execute)(_wrapper_state())


def test_a_plan_that_validated_and_executed_is_stored(store):
    out = _run_wrapped(store, {
        "ast_planner_response": ASTPlannerResponse(plan=_plan()),
        "generator_response": GeneratorResponse(sql_draft="SELECT 1"),
        "executor_response": _executed(_artifact()),
    })

    assert out["subgraph_outputs"]["sql_agent:sq1:t1"].plan_source == "llm"
    assert PlanCache(store).get(_sq()) == _plan()


def test_a_refused_plan_is_not_stored(store):
    denied = PipelineError(node="logical_validator", message="denied", severity=ErrorSeverity.CRITICAL,
                           error_code=ErrorCode.SECURITY_VIOLATION)
    _run_wrapped(store, {
        "ast_planner_response": ASTPlannerResponse(plan=_plan()),
        "executor_response": None,
        "errors": [denied],
    })

    assert PlanCache(store).get(_sq()) is None


def test_a_plan_that_failed_to_execute_is_not_stored(store):
    failed = PipelineError(node="executor", message="no such column", severity=ErrorSeverity.ERROR,
                           error_code=ErrorCode.EXECUTION_FAILED)
    _run_wrapped(store, {
        "ast_planner_response": ASTPlannerResponse(plan=_plan()),
        "generator_response": GeneratorResponse(sql_draft="SELECT nope"),
        "executor_response": _executed(None, [failed]),
        "errors": [failed],
    })

    assert PlanCache(store).get(_sq()) is None


def test_a_plan_only_run_stores_nothing(store):
    # It was never executed, so it has not earned a place.
    _run_wrapped(store, {
        "ast_planner_response": ASTPlannerResponse(plan=_plan()),
        "generator_response": GeneratorResponse(sql_draft="SELECT 1"),
    }, execute=False)

    assert PlanCache(store).get(_sq()) is None


def test_the_wrapper_reports_a_cached_plan_as_such(store):
    out = _run_wrapped(store, {
        "ast_planner_response": ASTPlannerResponse(plan=_plan(), plan_source="cache"),
        "generator_response": GeneratorResponse(sql_draft="SELECT 1"),
        "executor_response": _executed(_artifact()),
    })

    assert out["subgraph_outputs"]["sql_agent:sq1:t1"].plan_source == "cache"


# -- what the caller sees ------------------------------------------------------

def test_the_query_result_says_where_each_plan_came_from_and_counts_the_hits():
    from nl2sql.api.query_api import result_from_state
    from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput

    state = {"subgraph_outputs": {
        "sql_agent:sq1:t": SubgraphOutput(sub_query=_sq(), subgraph_id="sql_agent:sq1:t", plan=_plan(),
                                          sql_draft="SELECT 1", status="success", plan_source="cache"),
        "sql_agent:sq2:t": SubgraphOutput(sub_query=_sq(intent="other").model_copy(update={"id": "sq2"}),
                                          subgraph_id="sql_agent:sq2:t", plan=_plan(), sql_draft="SELECT 2",
                                          status="success", plan_source="llm"),
    }}

    result = result_from_state(state)

    assert {sq.id: sq.plan_source for sq in result.sub_queries} == {"sq1": "cache", "sq2": "llm"}
    assert result.usage.plan_cache_hits == 1


def test_trace_replay_uses_the_cache_only_when_the_recorded_run_did():
    # A replayed run must make the same LLM calls as the recorded one: a
    # recorded planner call has to be replayed, not answered from the cache.
    from nl2sql.tracing.replay import recorded_plan_cache_hit

    def doc(source):
        return {"nodes": [{"node": "ast_planner", "outputs": {"ast_planner_response": {"plan_source": source}}}]}

    assert recorded_plan_cache_hit(doc("cache")) is True
    assert recorded_plan_cache_hit(doc("llm")) is False
    assert recorded_plan_cache_hit({"nodes": [{"node": "ast_planner", "outputs": None}]}) is False
    assert recorded_plan_cache_hit({}) is False
