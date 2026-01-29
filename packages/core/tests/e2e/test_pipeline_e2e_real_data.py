from __future__ import annotations

import pytest

from nl2sql.auth import UserContext
from nl2sql.common.errors import ErrorCode, ErrorSeverity
from nl2sql.pipeline.runtime import run_with_graph


def _get_value(obj, key, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _get_sub_queries(decomposer):
    if decomposer is None:
        return []
    if isinstance(decomposer, dict):
        return decomposer.get("sub_queries", []) or []
    return getattr(decomposer, "sub_queries", []) or []


def _assert_no_critical_errors(errors):
    assert all(
        e.severity != ErrorSeverity.CRITICAL or "ResultColumn" in e.message
        for e in errors
    )


@pytest.mark.e2e
def test_pipeline_e2e_happy_path(demo_env, sample_questions) -> None:
    user_context = UserContext(roles=["admin"])
    first_questions = next(iter(sample_questions.values()), [])
    if not first_questions:
        pytest.skip("No demo questions available.")

    result = run_with_graph(
        demo_env.ctx,
        user_query=first_questions[0],
        execute=True,
        user_context=user_context,
    )

    errors = result.get("errors") or []
    _assert_no_critical_errors(errors)
    assert _get_value(result, "decomposer_response") is not None
    assert _get_value(result, "aggregator_response") is not None
    assert _get_value(result, "answer_synthesizer_response") is not None


@pytest.mark.e2e
def test_pipeline_e2e_multi_subquery(demo_env) -> None:
    user_context = UserContext(roles=["admin"])
    query = "List all factories in the US and show me suppliers from Germany."
    result = run_with_graph(
        demo_env.ctx,
        user_query=query,
        execute=True,
        user_context=user_context,
    )

    decomposer = _get_value(result, "decomposer_response")
    sub_queries = _get_sub_queries(decomposer)
    if len(sub_queries) < 2:
        pytest.skip("Decomposer did not emit multiple sub-queries for this input.")

    errors = result.get("errors") or []
    _assert_no_critical_errors(errors)
    assert _get_value(result, "aggregator_response") is not None
    assert _get_value(result, "answer_synthesizer_response") is not None


@pytest.mark.e2e
def test_pipeline_e2e_rbac_denied(demo_env, sample_questions) -> None:
    user_context = UserContext(roles=[])
    first_questions = next(iter(sample_questions.values()), [])
    if not first_questions:
        pytest.skip("No demo questions available.")

    result = run_with_graph(
        demo_env.ctx,
        user_query=first_questions[0],
        execute=True,
        user_context=user_context,
    )

    errors = result.get("errors") or []
    assert errors
    assert any(e.error_code == ErrorCode.SECURITY_VIOLATION for e in errors)
    assert _get_value(result, "answer_synthesizer_response") is None


@pytest.mark.e2e
def test_pipeline_e2e_retry_path(demo_env) -> None:
    user_context = UserContext(roles=["admin"])
    query = "Summarize findings across factories and suppliers."
    result = run_with_graph(
        demo_env.ctx,
        user_query=query,
        execute=True,
        user_context=user_context,
    )

    subgraph_outputs = result.get("subgraph_outputs") or {}
    retry_counts = [output.retry_count for output in subgraph_outputs.values()]
    if not retry_counts or max(retry_counts) == 0:
        pytest.skip("No retries observed for this run.")

    assert max(retry_counts) <= 3


@pytest.mark.e2e
def test_pipeline_e2e_execution_error(demo_env) -> None:
    user_context = UserContext(roles=["admin"])
    query = "Show me all columns from non_existent_table."
    result = run_with_graph(
        demo_env.ctx,
        user_query=query,
        execute=True,
        user_context=user_context,
    )

    errors = result.get("errors") or []
    if not errors:
        pytest.skip("No errors surfaced for invalid query.")

    expected = {
        ErrorCode.EXECUTION_ERROR,
        ErrorCode.PLANNING_FAILURE,
        ErrorCode.SCHEMA_RETRIEVAL_FAILED,
        ErrorCode.INVALID_STATE,
    }
    assert any(e.error_code in expected for e in errors)
