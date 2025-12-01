import sys
import os
from unittest.mock import MagicMock
from langchain_core.messages import AIMessageChunk
from langchain_core.runnables import RunnableLambda
from langchain_core.language_models import FakeListChatModel

# Add src to path
sys.path.append(os.path.join(os.getcwd(), "src"))

from nl2sql.langgraph_pipeline import run_with_graph
from nl2sql.datasource_config import DatasourceProfile
from nl2sql.schemas import PlanModel, IntentModel

# Mock Profile
profile = MagicMock(spec=DatasourceProfile)
profile.engine = "sqlite" # String for get_capabilities
profile.sqlalchemy_url = "sqlite:///:memory:"
profile.row_limit = 100

# Captured Logs
captured_tokens = []
captured_logs = []

def on_thought(node, logs, token=False):
    if token:
        captured_tokens.extend(logs)
    else:
        captured_logs.append(f"[{node}] {logs}")
        print(f"[{node}] {logs}")

# Mock Planner to fail (trigger Summarizer)
def fail_planner(prompt):
    raise ValueError("Intentional failure")

# Mock Intent
def mock_intent(prompt):
    return IntentModel(query_type="READ", keywords=[])

# Mock Summarizer LLM (Streaming)
summarizer_llm = FakeListChatModel(responses=["Feedback: Fix the plan."])

llm_map = {
    "intent": mock_intent,
    "planner": fail_planner,
    "summarizer": summarizer_llm,
    "generator": lambda x: None
}

print("Running graph to test streaming (updates mode)...")
try:
    run_with_graph(
        profile=profile,
        user_query="test",
        llm_map=llm_map,
        execute=False,
        on_thought=on_thought
    )
except Exception as e:
    print(f"Graph failed: {e}")

print("\nCaptured Tokens:", captured_tokens)
print("Captured Logs:", captured_logs)

if captured_tokens and captured_logs:
    print("SUCCESS: Tokens and Logs captured.")
else:
    print("FAILURE: Missing tokens or logs.")
