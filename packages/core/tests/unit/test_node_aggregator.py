
import unittest
from unittest.mock import MagicMock
from nl2sql.pipeline.nodes.aggregator.node import AggregatorNode
from nl2sql.pipeline.nodes.aggregator.schemas import AggregatedResponse
from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import ErrorCode

class TestAggregatorNode(unittest.TestCase):
    def setUp(self):
        self.mock_llm = MagicMock()
        self.node = AggregatorNode(self.mock_llm)
        self.node.chain = self.mock_llm # Bypass prompt chain

    def test_fast_path(self):
        """Test direct data return for single result with output_mode='data'."""
        state = GraphState(
            user_query="q",
            intermediate_results=[{"id": 1, "val": "A"}],
            output_mode="data"
        )
        
        result = self.node(state)
        
        self.assertEqual(result["final_answer"], {"id": 1, "val": "A"})
        self.assertIn("Fast path", result["reasoning"][0]["content"])

    def test_slow_path_llm(self):
        """Test LLM synthesis for complex or multiple results."""
        state = GraphState(
            user_query="q",
            intermediate_results=[{"id": 1}],
            output_mode="synthesis"
        )
        
        # Mock LLM Response
        self.mock_llm.invoke.return_value = AggregatedResponse(
            summary="Found 1 item.",
            content="Item details...",
            format_type="text"
        )
        
        result = self.node(state)
        
        self.assertIn("Found 1 item", result["final_answer"])
        self.assertIn("LLM Aggregation used", result["reasoning"][0]["content"])

    def test_slow_path_multiple_results(self):
        """Test that multiple results force LLM path even if mode is data (actually, does it?).
           Code says: if len(results) == 1 and not errors and mode == data -> Fast.
           So 2 results -> Slow.
        """
        state = GraphState(
            user_query="q",
            intermediate_results=[{"a": 1}, {"b": 2}],
            output_mode="data"
        )
        
        self.mock_llm.invoke.return_value = AggregatedResponse(summary="Multi", content="Multi", format_type="text")
        
        result = self.node(state)
        
        self.assertIn("LLM Aggregation used", result["reasoning"][0]["content"])

    def test_error_handling(self):
        """Test that exception behaves correctly."""
        self.mock_llm.invoke.side_effect = Exception("Boom")
        
        state = GraphState(user_query="q", intermediate_results=[], output_mode="synthesis")
        result = self.node(state)
        
        self.assertEqual(len(result["errors"]), 1)
        self.assertEqual(result["errors"][0].error_code, ErrorCode.AGGREGATOR_FAILED)
        self.assertIn("Boom", result["final_answer"])

if __name__ == "__main__":
    unittest.main()
