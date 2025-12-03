import pytest
from unittest.mock import MagicMock
from nl2sql.langgraph_pipeline import run_with_graph
from nl2sql.datasource_registry import DatasourceRegistry
from nl2sql.llm_registry import LLMRegistry
from nl2sql.nodes.decomposer_node import DecomposerResponse
from nl2sql.nodes.aggregator_node import AggregatedResponse

@pytest.fixture
def mock_registries():
    ds_registry = MagicMock(spec=DatasourceRegistry)
    llm_registry = MagicMock(spec=LLMRegistry)
    
    # Mock Intent LLM for Decomposer and Aggregator
    mock_intent_llm = MagicMock()
    llm_registry.intent_llm.return_value = mock_intent_llm
    
    # Mock Planner LLM
    llm_registry.planner_llm.return_value = MagicMock()
    llm_registry.summarizer_llm.return_value = MagicMock()
    
    return ds_registry, llm_registry, mock_intent_llm

from unittest.mock import patch
from langgraph.graph import StateGraph, END
from nl2sql.schemas import GraphState

@pytest.fixture
def mock_execution_subgraph():
    with patch("nl2sql.langgraph_pipeline.build_execution_subgraph") as mock_build:
        # Create a dummy subgraph that just appends a result
        def dummy_node(state: GraphState):
            return {"intermediate_results": [f"Result for {state.user_query}"]}
            
        workflow = StateGraph(GraphState)
        workflow.add_node("dummy", dummy_node)
        workflow.set_entry_point("dummy")
        workflow.add_edge("dummy", END)
        mock_build.return_value = workflow.compile()
        yield mock_build

def test_single_query_flow(mock_registries, mock_execution_subgraph):
    ds_registry, llm_registry, mock_intent_llm = mock_registries
    
    # Mock Decomposer response (Single Query)
    # Note: prompt | llm calls llm(input), so we mock the return value of the call, not invoke
    mock_intent_llm.with_structured_output.return_value.side_effect = [
        DecomposerResponse(sub_queries=["List machines"], reasoning="Single domain"),
        AggregatedResponse(summary="Found 5 machines.", format_type="list", content="Machine 1, Machine 2")
    ]
    
    result = run_with_graph(ds_registry, llm_registry, "List machines", execute=False, debug=True)
    
    assert "final_answer" in result
    assert "Found 5 machines" in result["final_answer"]
    assert result["sub_queries"] == ["List machines"]

def test_multi_query_flow(mock_registries, mock_execution_subgraph):
    ds_registry, llm_registry, mock_intent_llm = mock_registries
    
    # Mock Decomposer response (Multi Query)
    mock_intent_llm.with_structured_output.return_value.side_effect = [
        DecomposerResponse(sub_queries=["Query A", "Query B"], reasoning="Cross domain"),
        AggregatedResponse(summary="Combined result.", format_type="table", content="| Col | Val |\n|---|---|\n| A | 1 |")
    ]
    
    result = run_with_graph(ds_registry, llm_registry, "Compare A and B", execute=False, debug=True)
    
    assert "final_answer" in result
    assert "Combined result" in result["final_answer"]
    assert result["sub_queries"] == ["Query A", "Query B"]
    # Check if intermediate results were collected
    assert "intermediate_results" in result
    assert len(result["intermediate_results"]) == 2
    assert "Result for Query A" in result["intermediate_results"]
    assert "Result for Query B" in result["intermediate_results"]
