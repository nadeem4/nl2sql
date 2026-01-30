from types import SimpleNamespace
from unittest.mock import MagicMock

from nl2sql.pipeline.nodes.decomposer.node import DecomposerNode
from nl2sql.pipeline.nodes.decomposer.schemas import (
    DecomposerResponse,
    SubQuery,
    CombineGroup,
    CombineInput,
    PostCombineOp,
)
from nl2sql.pipeline.nodes.datasource_resolver.schemas import DatasourceResolverResponse, ResolvedDatasource
from nl2sql.pipeline.state import GraphState


def test_decomposer_maps_unmapped_and_stabilizes_ids():
    # Validates mapping because unsupported datasource ids must be unmapped.
    # Arrange
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    llm.invoke.return_value = DecomposerResponse(
        sub_queries=[
            SubQuery(id="sq1", intent="ok", datasource_id="allowed", metrics=[], filters=[], group_by=[], expected_schema=[]),
            SubQuery(id="sq2", intent="missing", datasource_id="", metrics=[], filters=[], group_by=[], expected_schema=[]),
            SubQuery(id="sq3", intent="unsupported", datasource_id="legacy", metrics=[], filters=[], group_by=[], expected_schema=[]),
        ],
        combine_groups=[
            CombineGroup(
                group_id="g1",
                operation="union",
                inputs=[CombineInput(subquery_id="sq1", role="left"), CombineInput(subquery_id="sq3", role="right")],
            )
        ],
        post_combine_ops=[
            PostCombineOp(
                op_id="op_stub",
                target_group_id="g1",
                operation="limit",
                limit=10,
                filters=[],
                metrics=[],
                group_by=[],
                order_by=[],
                expected_schema=[],
                metadata={},
            )
        ],
        unmapped_subqueries=[],
    )
    ctx = SimpleNamespace(llm_registry=MagicMock())
    ctx.llm_registry.get_llm.return_value = llm
    node = DecomposerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = llm.invoke.return_value

    state = GraphState(
        user_query="test",
        datasource_resolver_response=DatasourceResolverResponse(
            resolved_datasources=[
                ResolvedDatasource(datasource_id="allowed", metadata={}),
                ResolvedDatasource(datasource_id="legacy", metadata={}),
            ],
            allowed_datasource_ids=["allowed", "legacy"],
            unsupported_datasource_ids=["legacy"],
        ),
    )

    # Act
    result = node(state)["decomposer_response"]

    # Assert
    assert len(result.sub_queries) == 1
    assert result.sub_queries[0].datasource_id == "allowed"
    assert result.unmapped_subqueries
    assert result.post_combine_ops[0].op_id.startswith("op_")
