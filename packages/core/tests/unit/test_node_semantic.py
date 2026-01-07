
import unittest
from unittest.mock import MagicMock
from nl2sql.pipeline.nodes.semantic.node import SemanticAnalysisNode
from nl2sql.pipeline.nodes.semantic.schemas import SemanticAnalysisResponse
from nl2sql.pipeline.state import GraphState

class TestSemanticAnalysisNode(unittest.TestCase):
    def setUp(self):
        self.mock_llm = MagicMock()
        self.node = SemanticAnalysisNode(self.mock_llm)
        self.node.chain = self.mock_llm # Bypass chain

    def test_successful_analysis(self):
        """Test successful expansion."""
        mock_resp = SemanticAnalysisResponse(
            canonical_query="canonical",
            thought_process="thinking",
            keywords=["k1"],
            synonyms=["s1"],
            reasoning="reason"
        )
        self.mock_llm.invoke.return_value = mock_resp
        
        state = GraphState(user_query="raw")
        res = self.node(state)
        
        self.assertEqual(res["semantic_analysis"], mock_resp)
        self.assertEqual(res["reasoning"][0]["metadata"]["keywords"], ["k1"])
        
        self.mock_llm.invoke.assert_called_with({"user_query": "raw"})

    def test_error_resilience(self):
        """Test fallback when LLM fails."""
        self.mock_llm.invoke.side_effect = Exception("LLM Error")
        
        res = self.node.invoke("raw_query")
        
        # Should return a safe object, not raise
        self.assertEqual(res.canonical_query, "raw_query")
        self.assertEqual(res.keywords, [])
        self.assertIn("Analysis failed", res.reasoning)

if __name__ == "__main__":
    unittest.main()
