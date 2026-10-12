from typing import Dict, Optional
from langchain_core.runnables import Runnable, RunnableConfig
from langgraph.graph import END, StateGraph

from nl2sql.common.cancellation import CancellationToken
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode
from nl2sql.common.settings import settings
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
from nl2sql.pipeline.nodes.schema_retriever import SchemaRetrieverNode
from nl2sql.pipeline.nodes.validator import LogicalValidatorNode
from nl2sql.pipeline.nodes.refiner import RefinerNode
from nl2sql.pipeline.nodes.generator import GeneratorNode
from nl2sql.pipeline.nodes.executor import ExecutorNode
from nl2sql.datasources import DatasourceRegistry
from nl2sql.context import NL2SQLContext



import random
import time


def build_sql_agent_graph(
    ctx: NL2SQLContext,
    execute: bool = True,
):
    """Builds the SQL Agent Subgraph.

    Pipeline Flow:
    Planner -> LogicalValidator -> Generator -> Executor

    Feedback Loop:
    (LogicalValidator Error) -> RetryHandler -> Refiner -> Planner
    (Executor: query rejected by the database) -> RetryHandler -> Refiner -> Planner

    Args:
        ctx: The application context.
        execute: When False the executor is left out of the graph entirely, so
            a plan-only run never opens a database connection.
    """
    graph = StateGraph(SubgraphExecutionState)

    schema_retriever = SchemaRetrieverNode(ctx)
    ast_planner = ASTPlannerNode(ctx)
    logical_validator = LogicalValidatorNode(ctx)
    refiner = RefinerNode(ctx)
    generator = GeneratorNode(ctx)

    def _get_retry_count(state: SubgraphExecutionState) -> int:
        return state.retry_count

    def _token(config: Optional[RunnableConfig]) -> Optional[CancellationToken]:
        """Read the current run's cancellation token from the RunnableConfig.

        Returns None when invoked without a config (e.g. a direct unit-test call),
        which is treated as "not cancelled".
        """
        if not config:
            return None
        return (config.get("configurable") or {}).get("cancellation_token")

    def _is_cancelled(config: Optional[RunnableConfig]) -> bool:
        token = _token(config)
        return bool(token and token.is_cancelled())

    def retry_node(state: SubgraphExecutionState, config: Optional[RunnableConfig] = None) -> Dict:
        """Increments retry count with exponential backoff and jitter."""
        count = _get_retry_count(state)
        if _is_cancelled(config):
            return {
                "errors": [
                    PipelineError(
                        node="retry_handler",
                        message="Pipeline cancelled by user.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.CANCELLED,
                    )
                ]
            }
        if count >= settings.sql_agent_max_retries:
            return {"retry_count": count}

        base_delay = min(settings.sql_agent_retry_max_delay_sec, settings.sql_agent_retry_base_delay_sec * (2 ** count))
        jitter = random.uniform(0.0, settings.sql_agent_retry_jitter_sec)
        sleep_time = base_delay + jitter

        token = _token(config)
        if token is None:
            time.sleep(sleep_time)
        elif token.wait(timeout=sleep_time):
            return {
                "errors": [
                    PipelineError(
                        node="retry_handler",
                        message="Pipeline cancelled by user.",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.CANCELLED,
                    )
                ]
            }

        return {
            "retry_count": count + 1,
        }

    def check_planner(state: SubgraphExecutionState, config: Optional[RunnableConfig] = None) -> str:
        """Routes based on planner result."""
        if _is_cancelled(config):
            return "end"
        if not (state.ast_planner_response and state.ast_planner_response.plan):
            # If explicit errors exist, check retryability
            if state.errors:
                 if not all(e.is_retryable for e in state.errors):
                     return "end"

            if _get_retry_count(state) < settings.sql_agent_max_retries:
                return "retry"
            return "end"
        return "ok"

    def check_logical_validation(state: SubgraphExecutionState, config: Optional[RunnableConfig] = None) -> str:
        """Routes based on logical validation result.

        ERROR and CRITICAL findings block: non-retryable ones end the sub-query,
        retryable ones go to the refiner while retries remain, then end.
        WARNING findings (COLUMN_NOT_FOUND with strict columns off) do not block:
        the refiner gets the chance to repair the plan while retries remain, and
        once they are spent the plan proceeds to generation.
        """
        if _is_cancelled(config):
            return "end"
        findings = state.logical_validator_response.errors if state.logical_validator_response else []
        if not findings:
            return "ok"
        blocking = [e for e in findings if e.severity in (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL)]
        # Critical/Fatal errors stop execution immediately
        if not all(e.is_retryable for e in blocking):
            return "end"
        if _get_retry_count(state) < settings.sql_agent_max_retries:
            return "retry"
        return "end" if blocking else "ok"

    def check_execution(state: SubgraphExecutionState, config: Optional[RunnableConfig] = None) -> str:
        """Routes a query the database rejected back to the refiner.

        Only ``EXECUTION_FAILED`` is retried: the adapter marked the query
        itself as wrong (an unknown column, a type mismatch, bad syntax), so a
        new plan can fix it. A refusal (``DB_EXECUTION_ERROR``: permission,
        timeout, connection) or any other executor error ends the sub-query,
        as does running out of retries.

        An empty result filtered on a value its column does not have, with a
        close value to suggest, is retried the same way: a ``VALUE_NOT_FOUND``
        ERROR raised on this attempt (its ``details`` carry the attempt). The
        executor raises it at most once per sub-query.
        """
        if _is_cancelled(config):
            return "end"
        count = _get_retry_count(state)
        if count >= settings.sql_agent_max_retries:
            return "end"
        errors = getattr(state.executor_response, "errors", None) or []
        if errors:
            retryable = all(e.error_code == ErrorCode.EXECUTION_FAILED and e.is_retryable for e in errors)
            return "retry" if retryable else "end"
        missing_value = any(
            e.error_code == ErrorCode.VALUE_NOT_FOUND
            and e.severity == ErrorSeverity.ERROR
            and (e.details or {}).get("attempt") == count
            for e in state.errors
        )
        return "retry" if missing_value else "end"

    graph.add_node("schema_retriever", schema_retriever)
    graph.add_node("ast_planner", ast_planner)
    graph.add_node("logical_validator", logical_validator)
    graph.add_node("generator", generator)
    if execute:
        graph.add_node("executor", ExecutorNode(ctx))
    graph.add_node("refiner", refiner)
    graph.add_node("retry_handler", retry_node)

    graph.set_entry_point("schema_retriever")

    graph.add_edge("schema_retriever", "ast_planner")

    graph.add_conditional_edges(
        "ast_planner",
        check_planner,
        {"ok": "logical_validator", "retry": "retry_handler", "end": END},
    )

    graph.add_conditional_edges(
        "logical_validator",
        check_logical_validation,
        {"ok": "generator", "retry": "retry_handler", "end": END},
    )

    if execute:
        graph.add_edge("generator", "executor")
        graph.add_conditional_edges(
            "executor",
            check_execution,
            {"retry": "retry_handler", "end": END},
        )
    else:
        graph.add_edge("generator", END)

    graph.add_edge("retry_handler", "refiner")
    graph.add_edge("refiner", "ast_planner")

    return graph.compile()
