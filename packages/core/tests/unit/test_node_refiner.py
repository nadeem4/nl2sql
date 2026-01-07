
import unittest
from unittest.mock import MagicMock
from nl2sql.pipeline.nodes.refiner.node import RefinerNode
from nl2sql.pipeline.state import GraphState
from nl2sql.common.errors import PipelineError, ErrorCode, ErrorSeverity

class TestRefinerNode(unittest.TestCase):
    def setUp(self):
        self.mock_llm = MagicMock()
        self.node = RefinerNode(self.mock_llm)
        self.node.chain = self.mock_llm # Bypass chain for testing

    def test_missing_llm(self):
        """Test error when no LLM is provided."""
        node = RefinerNode(None)
        state = GraphState(user_query="q", errors=[])
        res = node(state)
        
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(res["errors"][0].error_code, ErrorCode.MISSING_LLM)

    def test_feedback_generation(self):
        """Test generating feedback from errors."""
        self.mock_llm.invoke.return_value = "Try using alias 'u'"
        
        state = GraphState(
            user_query="q",
            errors=[PipelineError(node="val", message="Unknown alias", error_code=ErrorCode.INVALID_ALIAS_USAGE, severity=ErrorSeverity.ERROR)],
            plan={"tables": []}
        )
        
        res = self.node(state)
        
        # Refiner returns a PipelineError with WARNING severity containing the feedback
        self.assertEqual(len(res["errors"]), 1)
        self.assertEqual(res["errors"][0].severity, ErrorSeverity.WARNING)
        self.assertEqual(res["errors"][0].message, "Try using alias 'u'")
        # self.assertIn("feedback", str(res["reasoning"]))

    def test_robustness_empty_state(self):
        """Test refiner works even with minimal state info."""
        self.mock_llm.invoke.return_value = "General advice"
        
        state = GraphState(user_query="q", errors=[]) # No plan, no tables
        
        res = self.node(state)
        self.assertEqual(res["errors"][0].message, "General advice")

if __name__ == "__main__":
    unittest.main()
