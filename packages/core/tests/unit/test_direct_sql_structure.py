import pytest
from unittest.mock import MagicMock
from nl2sql.pipeline.nodes.direct_sql.node import DirectSQLNode
from nl2sql.pipeline.nodes.direct_sql.schemas import DirectSQLResponse
from nl2sql.pipeline.state import GraphState
from nl2sql.datasources import DatasourceRegistry

def test_direct_sql_structured_output():
    # Mock LLM chain
    mock_llm = MagicMock()
    mock_registry = MagicMock(spec=DatasourceRegistry)
    
    # Mock behavior: chain.invoke returns a DirectSQLResponse object
    mock_response = DirectSQLResponse(
        sql="SELECT * FROM table t",
        reasoning="Selected table t"
    )
    mock_chain = MagicMock()
    mock_chain.invoke.return_value = mock_response
    
    # We need to inject this mock chain into the node manually 
    # since __init__ creates it from the llm_map.
    # So we mock the llm_map to return our mock_llm, but 
    # the node creates the chain using prompt | llm.
    # Easier to just instantiate the node and swap the chain.
    
    # Mock LLM Map
    mock_llm_callable = MagicMock()
    llm_map = {"direct_sql": mock_llm_callable}
    
    node = DirectSQLNode(llm_map=llm_map, registry=mock_registry)
    node.chain = mock_chain # Force swap
    
    # State
    state = GraphState(
        user_query="select stuff",
        relevant_tables=[],
        selected_datasource_id=None
    )
    
    # Act
    result = node(state)
    
    # Assert
    assert result["sql_draft"] == "SELECT * FROM table t"
    assert "reasoning" in result
    assert result["reasoning"][0]["content"] == "Selected table t"
    
    # Verify chain inputs
    mock_chain.invoke.assert_called_once()
    args = mock_chain.invoke.call_args[0][0]
    assert args["user_query"] == "select stuff"
    # Dialect defaults to TSQL
    assert args["dialect"] == "TSQL"

def test_direct_sql_error_handling():
    # Test that exceptions are caught
    mock_registry = MagicMock()
    llm_map = {"direct_sql": MagicMock()}
    node = DirectSQLNode(llm_map=llm_map, registry=mock_registry)
    
    # Force error
    node.chain = MagicMock()
    node.chain.invoke.side_effect = Exception("Parsing error")
    
    state = GraphState(user_query="fail", relevant_tables=[])
    result = node(state)
    
    assert "errors" in result
    assert result["errors"][0].message == "Direct SQL generation failed: Parsing error"
