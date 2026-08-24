from types import SimpleNamespace

from nl2sql.execution.executor.registry import ExecutorRegistry
from nl2sql.execution.executor.base import ExecutorService
from nl2sql.execution.contracts import ExecutorRequest, ExecutorResponse
from nl2sql_adapter_sdk.capabilities import DatasourceCapability


class FakeExecutor(ExecutorService):
    def execute(self, request: ExecutorRequest) -> ExecutorResponse:
        return ExecutorResponse(
            executor_name="fake",
            subgraph_name=request.subgraph_name,
            node_id=request.node_id,
            trace_id=request.trace_id,
        )


class FakeAdapter:
    def __init__(self, capabilities):
        self._capabilities = capabilities

    def capabilities(self):
        return self._capabilities


def test_registry_resolves_by_capability():
    # get_executor resolves a datasource id to its adapter's capabilities.
    adapters = {
        "sql_ds": FakeAdapter({DatasourceCapability.SUPPORTS_SQL}),
        "rest_ds": FakeAdapter({DatasourceCapability.SUPPORTS_REST}),
    }
    ds_registry = SimpleNamespace(get_adapter=lambda ds_id: adapters[ds_id])
    registry = ExecutorRegistry(ds_registry, register_defaults=False)
    executor = FakeExecutor()
    registry.register(DatasourceCapability.SUPPORTS_SQL, executor)

    resolved = registry.get_executor("sql_ds")
    assert resolved is executor

    unresolved = registry.get_executor("rest_ds")
    assert unresolved is None
