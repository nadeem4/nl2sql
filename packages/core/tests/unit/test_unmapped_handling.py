from unittest.mock import MagicMock

from nl2sql.pipeline.nodes.decomposer.schemas import UnmappedSubQuery
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.nodes.datasource_resolver.schemas import DatasourceResolverResponse, ResolvedDatasource
from nl2sql.pipeline.nodes.decomposer.node import DecomposerNode


def test_unmapped_subquery_accepts_detail_and_datasource():
    unmapped = UnmappedSubQuery(
        intent="Get revenue by month",
        reason="restricted_datasource",
        datasource_id="finance_db",
        detail="Datasource is not allowed for the current user context.",
    )

    assert unmapped.datasource_id == "finance_db"
    assert "allowed" in unmapped.detail


def test_decomposer_marks_missing_datasource_as_unmapped():
    ctx = MagicMock()
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx.llm_registry.get_llm.return_value = llm
    llm.invoke.return_value = MagicMock(
        sub_queries=[
            MagicMock(
                id="sq1",
                intent="Get revenue by month",
                datasource_id=None,
                metrics=[],
                filters=[],
                group_by=[],
                expected_schema=[],
            )
        ],
        combine_groups=[],
        post_combine_ops=[],
        unmapped_subqueries=[],
    )
    node = DecomposerNode(ctx)
    state = GraphState(
        user_query="test query",
        datasource_resolver_response=DatasourceResolverResponse(
            resolved_datasources=[ResolvedDatasource(datasource_id="sales_db", metadata={})],
            allowed_datasource_ids=["sales_db"],
        ),
    )

    result = node(state)

    response = result["decomposer_response"]
    assert response.sub_queries == []
    assert response.unmapped_subqueries
    assert response.unmapped_subqueries[0].reason == "no_datasource"


def test_decomposer_marks_unsupported_datasource_as_unmapped():
    ctx = MagicMock()
    llm = MagicMock()
    llm.with_structured_output.return_value = llm
    ctx.llm_registry.get_llm.return_value = llm
    llm.invoke.return_value = MagicMock(
        sub_queries=[
            MagicMock(
                id="sq1",
                intent="Get revenue by month",
                datasource_id="legacy_db",
                metrics=[],
                filters=[],
                group_by=[],
                expected_schema=[],
            )
        ],
        combine_groups=[],
        post_combine_ops=[],
        unmapped_subqueries=[],
    )
    node = DecomposerNode(ctx)
    state = GraphState(
        user_query="test query",
        datasource_resolver_response=DatasourceResolverResponse(
            resolved_datasources=[ResolvedDatasource(datasource_id="legacy_db", metadata={})],
            allowed_datasource_ids=["legacy_db"],
            unsupported_datasource_ids=["legacy_db"],
        ),
    )

    result = node(state)

    response = result["decomposer_response"]
    assert response.sub_queries == []
    assert response.unmapped_subqueries
    assert response.unmapped_subqueries[0].reason == "unsupported_datasource"
