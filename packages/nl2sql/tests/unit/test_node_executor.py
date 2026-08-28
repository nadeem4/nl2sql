from types import SimpleNamespace

from nl2sql.common.errors import ErrorCode
from nl2sql.execution.contracts import ExecutorRequest, ExecutorResponse
from nl2sql.execution.executor.sql_executor import SqlExecutorService
from nl2sql.pipeline.nodes.executor.node import ExecutorNode
from nl2sql_adapter_sdk.capabilities import DatasourceCapability


class FakeAdapter:
    def __init__(self, capabilities):
        self._capabilities = capabilities

    def capabilities(self):
        if isinstance(self._capabilities, Exception):
            raise self._capabilities
        return self._capabilities


def _ctx(adapters):
    return SimpleNamespace(
        ds_registry=SimpleNamespace(get_adapter=lambda ds_id: adapters[ds_id]),
        tenant_id="t1",
    )


def _state(datasource_id):
    return SimpleNamespace(
        trace_id="trace-1",
        subgraph_name="sql_agent",
        user_context=None,
        sub_query=SimpleNamespace(id="sq_1", datasource_id=datasource_id, schema_version="v1"),
        generator_response=SimpleNamespace(sql_draft="SELECT 1"),
    )


def _patch_execute(monkeypatch):
    def fake_execute(self, request: ExecutorRequest) -> ExecutorResponse:
        return ExecutorResponse(
            executor_name="fake-sql",
            subgraph_name=request.subgraph_name,
            node_id=request.node_id,
            trace_id=request.trace_id,
            tenant_id="t1",
        )

    monkeypatch.setattr(SqlExecutorService, "execute", fake_execute)


def test_executor_node_runs_sql_executor_for_sql_capable_datasource(monkeypatch):
    # Pins the capability gate: SUPPORTS_SQL reaches the SQL executor.
    # Arrange
    _patch_execute(monkeypatch)
    node = ExecutorNode(_ctx({"sql_ds": FakeAdapter({DatasourceCapability.SUPPORTS_SQL})}))

    # Act
    result = node(_state("sql_ds"))

    # Assert
    assert result["executor_response"].executor_name == "fake-sql"
    assert not result["errors"]


def test_executor_node_refuses_datasource_without_sql_capability(monkeypatch):
    # Pins the capability gate: no SUPPORTS_SQL means no executor, not a SQL run.
    # Arrange
    _patch_execute(monkeypatch)
    node = ExecutorNode(_ctx({"rest_ds": FakeAdapter({DatasourceCapability.SUPPORTS_REST})}))

    # Act
    result = node(_state("rest_ds"))

    # Assert
    assert result["executor_response"] is None
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert error.message == "No executor available for datasource 'rest_ds'."
    assert error.error_code == ErrorCode.INVALID_STATE


def test_executor_node_refuses_datasource_when_capability_lookup_raises(monkeypatch):
    # Pins the capability gate: a failing capabilities() call denies execution.
    # Arrange
    _patch_execute(monkeypatch)
    node = ExecutorNode(_ctx({"broken_ds": FakeAdapter(RuntimeError("boom"))}))

    # Act
    result = node(_state("broken_ds"))

    # Assert
    assert result["executor_response"] is None
    assert len(result["errors"]) == 1
    error = result["errors"][0]
    assert error.message == "No executor available for datasource 'broken_ds'."
    assert error.error_code == ErrorCode.INVALID_STATE
