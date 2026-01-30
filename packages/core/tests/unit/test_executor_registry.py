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


def test_registry_resolves_by_capability():
    registry = ExecutorRegistry(register_defaults=False)
    executor = FakeExecutor()
    registry.register(DatasourceCapability.SUPPORTS_SQL, executor)

    resolved = registry.get_executor({DatasourceCapability.SUPPORTS_SQL})
    assert resolved is executor

    unresolved = registry.get_executor({DatasourceCapability.SUPPORTS_REST})
    assert unresolved is None
