from types import SimpleNamespace

from datetime import datetime

from nl2sql.execution.contracts import ArtifactRef
from nl2sql.execution.execution_store import ExecutionStore
from nl2sql.pipeline.graph import build_graph
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.nodes.datasource_resolver.schemas import DatasourceResolverResponse, ResolvedDatasource
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse, SubQuery
from nl2sql.pipeline.nodes.global_planner.schemas import (
    GlobalPlannerResponse,
    ExecutionDAG,
    LogicalNode,
    RelationSchema,
    ColumnSpec,
)
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatorResponse
from nl2sql.pipeline.nodes.answer_synthesizer.schemas import AnswerSynthesizerResponse
from nl2sql_adapter_sdk.capabilities import DatasourceCapability
from nl2sql.pipeline.subgraphs.registry import SubgraphSpec


def _schema(columns):
    return RelationSchema(columns=[ColumnSpec(name=c) for c in columns])


def test_graph_runs_end_to_end_with_stubbed_subgraph(monkeypatch):
    # Validates graph wiring because pipeline integration depends on node routing.
    # Arrange
    class _Resolver:
        def __init__(self, ctx):
            self.ctx = ctx

        def __call__(self, state):
            response = DatasourceResolverResponse(
                resolved_datasources=[ResolvedDatasource(datasource_id="ds1", metadata={})],
                allowed_datasource_ids=["ds1"],
                unsupported_datasource_ids=[],
            )
            return {"datasource_resolver_response": response}

    class _Decomposer:
        def __init__(self, ctx):
            self.ctx = ctx

        def __call__(self, state):
            response = DecomposerResponse(
                sub_queries=[SubQuery(id="sq_1", intent="list users", datasource_id="ds1")],
                combine_groups=[],
                post_combine_ops=[],
                unmapped_subqueries=[],
            )
            return {"decomposer_response": response}

    class _Planner:
        def __init__(self, ctx):
            self.ctx = ctx

        def __call__(self, state):
            scan = LogicalNode(node_id="sq_1", kind="scan", inputs=[], output_schema=_schema(["id"]))
            dag = ExecutionDAG(nodes=[scan], edges=[])
            return {"global_planner_response": GlobalPlannerResponse(execution_dag=dag)}

    class _Aggregator:
        def __init__(self, ctx):
            self.ctx = ctx

        def __call__(self, state):
            return {"aggregator_response": AggregatorResponse(terminal_results={"sq_1": [{"id": 1}]})}

    class _Synthesizer:
        def __init__(self, ctx):
            self.ctx = ctx

        def __call__(self, state):
            return {"answer_synthesizer_response": AnswerSynthesizerResponse(final_answer={"summary": "ok"})}

    class _Subgraph:
        def invoke(self, _state):
            artifact = ArtifactRef(
                uri="file://stubbed.parquet",
                backend="local",
                format="parquet",
                row_count=1,
                columns=["id"],
                bytes=1,
                content_hash="stub",
                created_at=datetime.utcnow(),
                schema_version=None,
                path_template="<tenant_id>/<request_id>/<subgraph_name>/<dag_node_id>/<schema_version>/part-00000.parquet",
            )
            return {
                "sub_query_id": "sq_1",
                "executor_response": {"artifact": artifact},
                "errors": [],
            }

    monkeypatch.setattr("nl2sql.pipeline.graph.DatasourceResolverNode", _Resolver)
    monkeypatch.setattr("nl2sql.pipeline.graph.DecomposerNode", _Decomposer)
    monkeypatch.setattr("nl2sql.pipeline.graph.GlobalPlannerNode", _Planner)
    monkeypatch.setattr("nl2sql.pipeline.graph.EngineAggregatorNode", _Aggregator)
    monkeypatch.setattr("nl2sql.pipeline.graph.AnswerSynthesizerNode", _Synthesizer)
    monkeypatch.setattr(
        "nl2sql.pipeline.graph.build_subgraph_registry",
        lambda ctx: {
            "sql_agent": SubgraphSpec(
                name="sql_agent",
                required_capabilities={DatasourceCapability.SUPPORTS_SQL.value},
                builder=lambda _ctx: _Subgraph(),
            )
        },
    )

    ctx = SimpleNamespace(
        ds_registry=SimpleNamespace(get_capabilities=lambda _id: {DatasourceCapability.SUPPORTS_SQL.value}),
        execution_store=ExecutionStore(),
    )
    graph = build_graph(ctx)
    state = GraphState(user_query="list users")

    # Act
    output = graph.invoke(state.model_dump())

    # Assert
    assert output["answer_synthesizer_response"].final_answer["summary"] == "ok"
    assert output["artifact_refs"]["sq_1"].row_count == 1
