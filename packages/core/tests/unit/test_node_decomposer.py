
import unittest
from unittest.mock import MagicMock

from nl2sql.pipeline.nodes.decomposer.node import DecomposerNode
from nl2sql.pipeline.nodes.decomposer.schemas import (
    DecomposerResponse,
    SubQuery,
    CombineGroup,
    CombineInput,
    ExpectedColumn,
)
from nl2sql.pipeline.state import GraphState
from nl2sql.pipeline.nodes.datasource_resolver.schemas import DatasourceResolverResponse, ResolvedDatasource
from nl2sql.auth import UserContext

class TestDecomposerNode(unittest.TestCase):
    def setUp(self):
        self.mock_llm = MagicMock()
        self.mock_llm.with_structured_output.return_value = MagicMock()
        self.mock_llm_registry = MagicMock()
        self.mock_llm_registry.get_llm.return_value = self.mock_llm
        self.ctx = MagicMock()
        self.ctx.llm_registry = self.mock_llm_registry

        self.node = DecomposerNode(self.ctx)
        
        # Default valid state
        self.state = GraphState(
            user_query="Show me sales",
            user_context=UserContext(),
            datasource_resolver_response=DatasourceResolverResponse(
                resolved_datasources=[ResolvedDatasource(datasource_id="sales_db", metadata={})],
                allowed_datasource_ids=["sales_db"],
            ),
        )

    def test_success_flow(self):
        """Test successful decomposition with semantic outputs."""
        llm_resp = DecomposerResponse(
            sub_queries=[
                SubQuery(
                    id="sq_1",
                    datasource_id="sales_db",
                    intent="total sales by region",
                    metrics=[],
                    filters=[],
                    group_by=[],
                    expected_schema=[ExpectedColumn(name="total_sales", dtype="float")],
                )
            ],
            combine_groups=[
                CombineGroup(
                    group_id="cg_1",
                    operation="standalone",
                    inputs=[CombineInput(subquery_id="sq_1", role="base")],
                )
            ],
            post_combine_ops=[],
            unmapped_subqueries=[],
        )
        self.mock_llm.invoke.return_value = llm_resp
        self.node.chain = self.mock_llm # Bypass prompt chain for direct mock
        
        result = self.node(self.state)

        response = result["decomposer_response"]
        self.assertEqual(len(response.sub_queries), 1)
        sq = response.sub_queries[0]
        self.assertEqual(sq.datasource_id, "sales_db")
        self.assertEqual(sq.intent, "total sales by region")
        self.assertTrue(sq.id.startswith("sq_"))
        self.assertEqual(response.combine_groups[0].inputs[0].subquery_id, sq.id)

if __name__ == "__main__":
    unittest.main()
