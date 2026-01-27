
import pytest
from unittest.mock import MagicMock, patch, call

from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph
from nl2sql.common.errors import ErrorCode
from nl2sql.pipeline.nodes.ast_planner.schemas import PlanModel, TableRef
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.schema import Table
from nl2sql.auth import UserContext

class TestSQLAgentRetry:
    
    @pytest.fixture
    def mock_ctx(self):
        ctx = MagicMock()
        ctx.ds_registry = MagicMock()
        ctx.rbac = MagicMock()
        ctx.rbac.get_allowed_tables.return_value = ["sales_db.sales"]
        ctx.llm_registry = MagicMock()
        return ctx

    @pytest.fixture
    def mock_llm_map(self):
        planner_llm = MagicMock()
        planner_runner = MagicMock()
        planner_llm.with_structured_output.return_value = planner_runner
        refiner_llm = MagicMock()
        return {
            "ast_planner": planner_llm,
            "ast_planner_runner": planner_runner,
            "refiner": refiner_llm,
        }

    @patch("nl2sql.pipeline.subgraphs.sql_agent.time.sleep")
    @patch("nl2sql.pipeline.subgraphs.sql_agent.random.uniform")
    def test_retry_backoff_logic(self, mock_jitter, mock_sleep, mock_ctx, mock_llm_map):
        """Test that retry_node calculates sleep time correctly."""
        # Setup fixed jitter
        mock_jitter.return_value = 0.5
        
        # Build graph
        def get_llm(name):
            if name == "ast_planner":
                return mock_llm_map["ast_planner"]
            if name == "refiner":
                return mock_llm_map["refiner"]
            return MagicMock()

        mock_ctx.llm_registry.get_llm.side_effect = get_llm

        # Planner fails to trigger retry
        mock_llm_map["ast_planner_runner"].invoke.side_effect = Exception("LLM Auto Fail")

        graph = build_sql_agent_graph(mock_ctx)
        
        # We want to trigger the retry_handler node.
        # Since retry_node is internal, we can't unit test it directly easily.
        # But we can simulate the state transition if we could invoke just that node?
        # LangGraph nodes are runnables. We can access them if we know where they are stored.
        # graph.nodes["retry_handler"] ? usually graph is a CompiledGraph.
        
        # Accessing private members of LangGraph might be brittle.
        # Instead, let's run the whole graph but force a retry scenario.
        # 1. Planner fails (returns None plan).
        # 2. Check_planner returns "retry".
        # 3. Retry_handler runs -> calls sleep.
        # 4. Refiner runs.
        # 5. Planner runs again (we need to stop the loop or let it run out).
        
        # Let's verify Backoff logic by calling the node function directly if we can't import it?
        # WE CAN'T import inner functions.
        
        # BUT, we can use the 'graph' object to find the node.
        # In LangGraph, nodes are stored in `graph.nodes`.
        retry_node = graph.nodes["retry_handler"]
        
        # Test Retry 0 -> 1
        state_0 = SubgraphExecutionState(
            trace_id="t",
            sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        )
        # The node might expect a dict or state object, LangGraph wraps it.
        # Usually checking the source, retry_node takes state and returns dict.
        # If CompiledGraph wrapping is complex, this might fail, but let's try invoking the runnable associated.
        
        # Note: In current LangGraph, graph.nodes["name"] returns the Node object, 
        # which has a .runnable or is a runnable/function.
        # Let's assume it's callable for now or extract the callable.
        
        # HACK: If accessing inner function is too hard via the graph object, 
        # we can just reproduce the logic test here if strictly required, 
        # OR run the full graph with a mock side_effect that counts sleeps.
        
        # Let's try running the graph logic.
        
        # Setup Planner to always fail (return Plan=None)
        # We need to simulate the GraphState flow.
        # To do that, the PlannerNode needs to return a state that triggers retry.
        
        # Mock Planner Node behavior:
        # We can't easily mock the 'planner' node instance since it's created inside build_function.
        # But we pass mock_llm_map["planner"].
        # If llm returns None or raises, Planner handles it.
        
        # We want Planner to return { "errors": [RETRYABLE], "plan": None }
        # PlannerNode logic: if LLM fails, it catches exception and returns PLANNING_FAILURE (Retryable).
        # Run graph
        # Cap max execution to avoid infinite loops if logic is broken (recursion_limit)
        initial_state = SubgraphExecutionState(
            user_context=UserContext(),
            trace_id="t",
            sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="test"),
        )
        
        try:
            graph.invoke(initial_state, config={"recursion_limit": 10})
        except Exception:
            pass # We expect it to fail after retries exhaust
            
        # Assertion: sleep should be called 3 times (0->1, 1->2, 2->3)
        # Retry 0: base=1.0, jitter=0.5 -> 1.5s
        # Retry 1: base=2.0, jitter=0.5 -> 2.5s
        # Retry 2: base=4.0, jitter=0.5 -> 4.5s
        # Retry 3: End (no sleep needed before end, or maybe it sleeps then checks? 
        # check_planner logic: if retry_count < 3: return "retry".
        # So:
        # cnt=0 -> check -> retry -> handler (sleeps, cnt=1) -> refiner -> planner
        # cnt=1 -> check -> retry -> handler (sleeps, cnt=2) -> refiner -> planner
        # cnt=2 -> check -> retry -> handler (sleeps, cnt=3) -> refiner -> planner
        # cnt=3 -> check -> end.
        
        assert mock_sleep.call_count == 3
        
        # Check values
        calls = mock_sleep.call_args_list
        # Call 1: 1.0 + 0.5 = 1.5
        assert calls[0] == call(1.5)
        # Call 2: 2.0 + 0.5 = 2.5
        assert calls[1] == call(2.5)
        # Call 3: 4.0 + 0.5 = 4.5
        assert calls[2] == call(4.5)

    @patch("nl2sql.pipeline.subgraphs.sql_agent.time.sleep")
    def test_fail_fast_on_fatal_error(self, mock_sleep, mock_ctx, mock_llm_map):
        """Test that Fatal errors do NOT trigger retries."""
        
        # We need the Planner (or Validator) to return a FATAL error.
        # Planner Node handles exceptions by returning PLANNING_FAILURE (retryable).
        # We need it to return SECURITY_VIOLATION.
        # We can mock the LLM to return a "Valid" plan that triggers a Security Violation in LogicalValidator?
        # OR we can mock the `check_planner` logic? No, check_planner is inner.
        
        # Strategy: Valid Plan -> Logical Validator -> Security Violation -> Fail Fast.
        
        # 1. Planner returns a valid plan (so we pass check_planner).
        mock_plan = PlanModel(query_type="READ", tables=[], select_items=[], joins=[])
        # Ensure both invoke and __call__ return the plan
        mock_llm_map["ast_planner_runner"].invoke.return_value = mock_plan
        
        # 2. Logical Validator needs to produce SECURITY_VIOLATION.
        # LogicalValidator uses `registry`. We can't easily mock the inner LogicValidator behavior 
        # unless we give it a plan that fails policy.
        # A plan with table not in allowed_tables causes SECURITY_VIOLATION.
        
        mock_ctx.rbac.get_allowed_tables.return_value = ["sales_db.sales"]

        def get_llm(name):
            if name == "ast_planner":
                return mock_llm_map["ast_planner"]
            if name == "refiner":
                return mock_llm_map["refiner"]
            return MagicMock()

        mock_ctx.llm_registry.get_llm.side_effect = get_llm

        graph = build_sql_agent_graph(mock_ctx)

        # Plan queries "secret_table"
        bad_table_sdk = Table(name="secret_table", columns=[])
        bad_table_ref = TableRef(name="secret_table", alias="t", ordinal=0)
        
        mock_plan.tables = [bad_table_ref]
        
        state = SubgraphExecutionState(
             sub_query=SubQuery(id="sq1", datasource_id="sales_db", intent="read secret"),
             user_context=UserContext(roles=["user"]),
             relevant_tables=[bad_table_sdk],
             trace_id="t",
        )
        
        res = graph.invoke(state)
        
        # Expectation:
        # Planner -> OK
        # LogicalValidator -> Error(SECURITY_VIOLATION)
        # Check_Logic -> End (because fatal)
        # Should NOT call retry_handler (no sleep)
        
        assert mock_sleep.call_count == 0
        assert "errors" in res
        assert res["errors"][0].error_code == ErrorCode.SECURITY_VIOLATION

