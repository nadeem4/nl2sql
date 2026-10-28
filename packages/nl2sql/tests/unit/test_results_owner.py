"""A run's results are readable only by the caller who asked (docs/security/model.md, "Results").

The manifest a run writes records its asker, ``(tenant_id, user_id)``: the caller's tenant, else the engine's,
and the caller's user id, None for an anonymous caller. An anonymous caller (every REST caller today) also has
its sorted roles recorded, so only a caller with the same roles reads the run; a named user's run is bound to
``(tenant_id, user_id)`` alone. Every read (``manifest``, ``page``, ``csv``, ``chart``,
``head``) goes through the manifest and needs the caller: another user's or another tenant's trace id is the
same ``ResultNotFound`` as a missing run, a manifest with no asker recorded is refused, and a read with no
caller is a ``TypeError``.
"""
from __future__ import annotations

import json

import pytest

from nl2sql import NL2SQL, ResultNotFound, UserContext
from nl2sql.execution.artifacts import ArtifactStore
from nl2sql.execution.artifacts.results import ResultOwner, ResultReader
from nl2sql.execution.dag import ExecutionDAG, LogicalEdge, LogicalNode
from nl2sql.hooks import Allow, HookBinding, HookRunner
from nl2sql.pipeline.nodes.aggregator.node import EngineAggregatorNode
from nl2sql.pipeline.nodes.answer_synthesizer.node import release_results
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.subgraphs.schemas import SubgraphOutput
from nl2sql.storage.local import LocalResults
from nl2sql.testing.runtime import fake_runtime
from nl2sql_sdk.contracts import ResultFrame

TEMPLATE = "<tenant_id>/<request_id>/<sub_query_id>.parquet"
TENANT = "t1"
RESULT = "combine_g1"
ASKER = UserContext(user_id="ann", roles=["analyst"])
ROWS = [{"month": f"2024-0{m}", "revenue": 100 * m} for m in (1, 2, 3)]


@pytest.fixture()
def store(tmp_path):
    return ArtifactStore(LocalResults(str(tmp_path)), path_template=TEMPLATE)


def _state(store, user, request="run-1"):
    ref = store.create_artifact_ref(ResultFrame.from_row_dicts(ROWS),
                                    {"tenant_id": TENANT, "request_id": request, "sub_query_id": "sq1"})
    output = SubgraphOutput(subgraph_id="sg1", sub_query=SubQuery(id="sq1", datasource_id="d", intent="i"),
                            artifact=ref)
    dag = ExecutionDAG(
        nodes=[LogicalNode(node_id="sq1", kind="scan", inputs=[]),
               LogicalNode(node_id=RESULT, kind="combine", inputs=["sq1"],
                           attributes={"operation": "standalone", "join_keys": []})],
        edges=[LogicalEdge(edge_id="e1", from_id="sq1", to_id=RESULT)])
    return GraphState(trace_id=request, user_query="q", execution_dag=dag, artifact_refs={"sq1": ref},
                      subgraph_outputs={"sg1": output}, user_context=user)


def _run(store, user=ASKER, request="run-1"):
    """The aggregator over one scan, run as ``user``: it saves the result and writes the manifest."""
    EngineAggregatorNode(fake_runtime(settings={"tenant_id": TENANT}, artifact_store=store))(
        _state(store, user, request))


def _engine(store):
    engine = NL2SQL.__new__(NL2SQL)
    engine._runtime = fake_runtime(settings={"tenant_id": TENANT}, artifact_store=store)
    return engine


def _reads(engine, store, user):
    """Every read of run-1, as ``user``."""
    owner = ResultOwner.of(user, TENANT)
    return [lambda: engine.results.manifest("run-1", user_context=user),
            lambda: engine.results.page("run-1", RESULT, user_context=user),
            lambda: b"".join(engine.results.csv("run-1", RESULT, user_context=user)),
            lambda: engine.results.chart("run-1", user_context=user),
            lambda: ResultReader(store, TENANT, owner).head("run-1", RESULT, 10)]


def test_the_manifest_records_who_asked(store):
    _run(store)

    assert store.read_manifest(TENANT, "run-1")["owner"] == {"tenant_id": TENANT, "user_id": "ann", "roles": None}


def test_the_manifest_records_a_callers_own_tenant(store):
    _run(store, UserContext(user_id="ann", tenant_id="acme"))

    assert store.read_manifest(TENANT, "run-1")["owner"] == {"tenant_id": "acme", "user_id": "ann", "roles": None}


def test_the_manifest_written_after_answer_after_records_who_asked(store):
    hooks = HookRunner([HookBinding(name="review", hook=lambda ctx: Allow(), points=frozenset({"answer.after"}))])
    ctx = fake_runtime(settings={"tenant_id": TENANT}, artifact_store=store, hooks=hooks)
    state = _state(store, ASKER)
    update = EngineAggregatorNode(ctx)(state)
    assert store.read_manifest(TENANT, "run-1") is None, "withheld until answer.after allowed"

    release_results(ctx, state.model_copy(update={"aggregator_response": update["aggregator_response"]}))

    assert store.read_manifest(TENANT, "run-1")["owner"] == {"tenant_id": TENANT, "user_id": "ann", "roles": None}


def test_the_asker_reads_their_results(store):
    _run(store)
    engine = _engine(store)

    manifest, page, csv, chart, head = (read() for read in _reads(engine, store, ASKER))

    assert manifest.results[0].node_id == RESULT
    assert page.total_rows == 3 and b"revenue" in csv
    assert chart.data.trace_id == "run-1" and len(head[1]) == 3


@pytest.mark.parametrize("other", [
    UserContext(user_id="bob", roles=["analyst"]),
    UserContext(user_id="ann", tenant_id="t2", roles=["analyst"]),
    UserContext(roles=["admin"]),
], ids=["another user", "another tenant", "an anonymous caller"])
def test_another_callers_trace_id_is_not_found(store, other):
    _run(store)
    engine = _engine(store)

    for read in _reads(engine, store, other):
        with pytest.raises(ResultNotFound):
            read()


def test_a_chart_of_a_query_result_checks_the_caller_too(store):
    from nl2sql import QueryResult
    from nl2sql.execution.artifacts.results import ResultSummary

    _run(store)
    result = QueryResult(trace_id="run-1", result_manifest=[ResultSummary(node_id=RESULT)])

    with pytest.raises(ResultNotFound):
        _engine(store).results.chart(result, user_context=UserContext(user_id="bob"))
    assert _engine(store).results.chart(result, user_context=ASKER).data.trace_id == "run-1"


def test_the_manifest_records_an_anonymous_callers_sorted_roles(store):
    _run(store, UserContext(roles=["viewer", "admin", "viewer"]))

    assert store.read_manifest(TENANT, "run-1")["owner"] == {
        "tenant_id": TENANT, "user_id": None, "roles": ["admin", "viewer"]}


def test_a_named_users_run_is_bound_to_the_user_not_the_role(store):
    _run(store)

    reader = UserContext(user_id="ann", roles=["viewer"])
    manifest, page, csv, chart, head = (read() for read in _reads(_engine(store), store, reader))
    assert page.total_rows == 3 and len(head[1]) == 3


def test_an_anonymous_run_is_readable_by_an_anonymous_caller_of_its_tenant_with_the_same_roles(store):
    _run(store, UserContext(roles=["admin", "analyst"]))
    engine = _engine(store)

    for same in (UserContext(roles=["analyst", "admin"]), UserContext(tenant_id=TENANT, roles=["admin", "analyst"])):
        manifest, page, csv, chart, head = (read() for read in _reads(engine, store, same))
        assert page.total_rows == 3 and b"revenue" in csv and len(head[1]) == 3


@pytest.mark.parametrize("other", [
    UserContext(roles=["viewer"]),
    UserContext(roles=["admin"]),
    UserContext(roles=["admin", "analyst", "viewer"]),
    UserContext(user_id="ann", roles=["admin", "analyst"]),
    UserContext(tenant_id="t2", roles=["admin", "analyst"]),
], ids=["another role", "fewer roles", "more roles", "a named user", "another tenant"])
def test_an_anonymous_run_is_not_found_by_any_other_caller(store, other):
    _run(store, UserContext(roles=["admin", "analyst"]))
    engine = _engine(store)

    for read in _reads(engine, store, other):
        with pytest.raises(ResultNotFound):
            read()


@pytest.mark.parametrize("read", [
    lambda r: r.manifest("run-1"),
    lambda r: r.page("run-1", RESULT),
    lambda r: r.csv("run-1", RESULT),
    lambda r: r.chart("run-1"),
    lambda r: r.manifest("run-1", user_context=None),
    lambda r: r.page("run-1", RESULT, user_context="ann"),
], ids=["manifest", "page", "csv", "chart", "None", "not a UserContext"])
def test_a_read_with_no_caller_is_a_type_error(store, read):
    _run(store)

    with pytest.raises(TypeError, match="user_context"):
        read(_engine(store).results)


def test_a_manifest_with_no_asker_recorded_is_refused(store):
    _run(store)
    doc = store.read_manifest(TENANT, "run-1")
    del doc["owner"]
    store.results.write(store._manifest_key(TENANT, "run-1"), [json.dumps(doc).encode()])
    assert "owner" not in store.read_manifest(TENANT, "run-1")

    for read in _reads(_engine(store), store, ASKER):
        with pytest.raises(ResultNotFound):
            read()
