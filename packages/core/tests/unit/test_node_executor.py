from types import SimpleNamespace

import pytest

from nl2sql.pipeline.nodes.executor.node import ExecutorNode
from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse
from nl2sql.pipeline.nodes.decomposer.schemas import SubQuery
from nl2sql.pipeline.state import SubgraphExecutionState
from nl2sql.common.contracts import ExecutionResult
from nl2sql.common.errors import ErrorCode
from nl2sql_adapter_sdk.capabilities import DatasourceCapability


def test_executor_requires_sql_and_datasource():
    # Validates required inputs because execution must fail closed.
    # Arrange
    adapter = SimpleNamespace(capabilities=lambda: {DatasourceCapability.SUPPORTS_SQL})
    ctx = SimpleNamespace(
        ds_registry=SimpleNamespace(
            get_adapter=lambda _id: adapter,
            get_capabilities=lambda _id: {DatasourceCapability.SUPPORTS_SQL.value},
        )
    )
    node = ExecutorNode(ctx)

    # Act
    missing_sql = node(
        SubgraphExecutionState(
            trace_id="t",
            sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
            generator_response=GeneratorResponse(sql_draft=None),
        )
    )
    missing_ds = node(
        SubgraphExecutionState(
            trace_id="t",
            sub_query=None,
            generator_response=GeneratorResponse(sql_draft="SELECT 1"),
        )
    )

    # Assert
    assert missing_sql["errors"][0].error_code == ErrorCode.MISSING_SQL
    assert missing_ds["errors"][0].error_code == ErrorCode.MISSING_DATASOURCE_ID


def test_executor_enforces_max_bytes(monkeypatch):
    # Validates safeguard enforcement because oversized payloads must be blocked.
    # Arrange
    adapter = SimpleNamespace(
        datasource_engine_type="sqlite",
        connection_args={},
        statement_timeout_ms=1000,
        row_limit=10,
        max_bytes=10,
        capabilities=lambda: {DatasourceCapability.SUPPORTS_SQL},
    )
    ctx = SimpleNamespace(
        ds_registry=SimpleNamespace(
            get_adapter=lambda _id: adapter,
            get_capabilities=lambda _id: {DatasourceCapability.SUPPORTS_SQL.value},
        )
    )
    node = ExecutorNode(ctx)

    monkeypatch.setattr("nl2sql.execution.executor.sql_executor.get_execution_pool", lambda: SimpleNamespace())
    monkeypatch.setattr(
        "nl2sql.execution.executor.sql_executor.execute_in_sandbox",
        lambda *_a, **_k: ExecutionResult(
            success=True,
            data={
                "row_count": 1,
                "rows": [{"id": 1}],
                "columns": ["id"],
                "bytes_returned": 100,
            },
        ),
    )
    monkeypatch.setattr("nl2sql.execution.executor.sql_executor.DB_BREAKER", lambda fn: fn)

    state = SubgraphExecutionState(
        trace_id="t",
        sub_query=SubQuery(id="sq1", datasource_id="ds1", intent="q"),
        generator_response=GeneratorResponse(sql_draft="SELECT 1"),
    )

    # Act
    result = node(state)

    # Assert
    assert result["errors"][0].error_code == ErrorCode.SAFEGUARD_VIOLATION
