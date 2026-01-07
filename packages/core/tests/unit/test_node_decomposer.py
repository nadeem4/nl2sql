
import unittest
from unittest.mock import MagicMock, ANY
from nl2sql.pipeline.nodes.decomposer.node import DecomposerNode
from nl2sql.pipeline.nodes.decomposer.schemas import DecomposerResponse, SubQuery
from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import ErrorCode
from nl2sql_adapter_sdk import Table, Column

class TestDecomposerNode(unittest.TestCase):
    def setUp(self):
        self.mock_llm = MagicMock()
        self.mock_registry = MagicMock()
        self.mock_store = MagicMock()
        self.node = DecomposerNode(self.mock_llm, self.mock_registry, self.mock_store)
        
        # Default valid state
        self.state = GraphState(
            user_query="Show me sales",
            user_context={"allowed_datasources": ["sales_db"]}
        )

    def test_authz_denial(self):
        """Test that requests are rejected if no datasources are allowed."""
        state = GraphState(
            user_query="Show me sales", 
            user_context={} # Empty context
        )
        
        result = self.node(state)
        
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0].error_code, ErrorCode.SECURITY_VIOLATION)
        self.assertEqual(result["confidence"], 0.0)

    def test_retrieval_empty(self):
        """Test handling when vector store returns no documents."""
        self.mock_store.retrieve_routing_context.return_value = []
        
        result = self.node(self.state)
        
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0].error_code, ErrorCode.SCHEMA_RETRIEVAL_FAILED)
        self.mock_store.retrieve_routing_context.assert_called_with("Show me sales", k=20, datasource_id=["sales_db"])

    def test_success_flow(self):
        """Test successful decomposition with relevant tables."""
        # Mock Vector Store Response
        table_schema = Table(name="sales", columns=[Column(name="amount", type="int")]).model_dump_json()
        doc = MagicMock()
        doc.metadata = {
            "type": "table", 
            "datasource_id": "sales_db", 
            "table_name": "sales",
            "schema_json": table_schema
        }
        doc.page_content = "Table: sales..."
        self.mock_store.retrieve_routing_context.return_value = [doc]
        
        # Mock LLM Response
        llm_resp = DecomposerResponse(
            sub_queries=[SubQuery(query="SELECT * FROM sales", datasource_id="sales_db", complexity="simple")],
            confidence=0.9,
            output_mode="data",
            reasoning="Found table"
        )
        self.mock_llm.invoke.return_value = llm_resp
        self.node.chain = self.mock_llm # Bypass prompt chain for direct mock
        
        result = self.node(self.state)
        
        self.assertEqual(len(result["sub_queries"]), 1)
        sq = result["sub_queries"][0]
        self.assertEqual(sq.datasource_id, "sales_db")
        # useful logic check: relevant_tables should be populated from cache
        self.assertEqual(len(sq.relevant_tables), 1)
        self.assertEqual(sq.relevant_tables[0].name, "sales")
        self.assertEqual(result["confidence"], 0.9)

    def test_semantic_expansion(self):
        """Test that semantic analysis results expand the retrieval query."""
        mock_analysis = MagicMock()
        mock_analysis.keywords = ["revenue"]
        mock_analysis.synonyms = ["income"]
        mock_analysis.canonical_query = "List sales"
        
        self.state.semantic_analysis = mock_analysis
        
        # Mock non-empty retrieval so we don't fail early
        self.mock_store.retrieve_routing_context.return_value = [MagicMock(metadata={"type":"example"})]
        
        self.node.chain = MagicMock()
        self.node.chain.invoke.return_value = DecomposerResponse(sub_queries=[], confidence=0, output_mode="synthesis", reasoning="")

        self.node(self.state)
        
        # Expected Query: "Canonical Keywords Synonyms"
        expected_query = "List sales revenue income"
        self.mock_store.retrieve_routing_context.assert_called_with(expected_query, k=20, datasource_id=["sales_db"])

if __name__ == "__main__":
    unittest.main()
