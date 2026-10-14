from nl2sql.adapters.sqlalchemy_base.adapter import BaseSQLAlchemyAdapter
from nl2sql_sdk import ConnectionModel, Limits
from nl2sql_sdk.contracts import AdapterRequest


class _Memory(ConnectionModel):
    pass


class _TestAdapter(BaseSQLAlchemyAdapter):
    title = "Test"
    connection_model = _Memory

    def url(self, connection):
        return "sqlite:///:memory:"

    def explain(self, sql: str):
        raise NotImplementedError

    def cost_estimate(self, sql: str):
        raise NotImplementedError

    def get_dialect(self) -> str:
        return "sqlite"

    @property
    def exclude_schemas(self):
        return set()


def test_execute_rejects_non_sql_requests():
    # Validates error normalization because callers depend on consistent error codes.
    # Arrange
    adapter = _TestAdapter("ds1", _Memory(), Limits())

    # Act
    response = adapter.execute(AdapterRequest(plan_type="rest", payload={"sql": "SELECT 1"}))

    # Assert
    assert response.success is False
    assert response.error.error_code == "CAPABILITY_VIOLATION"


def test_execute_rejects_missing_sql():
    # Validates missing SQL handling because adapter must fail closed safely.
    # Arrange
    adapter = _TestAdapter("ds1", _Memory(), Limits())

    # Act
    response = adapter.execute(AdapterRequest(plan_type="sql", payload={}))

    # Assert
    assert response.success is False
    assert response.error.error_code == "MISSING_SQL"


def test_limits_are_kept_for_the_adapter_to_enforce():
    # SQLAlchemy ignores execution_options["timeout"], so the base adapter only
    # keeps the limits; each adapter's build_engine() enforces the timeout.
    limits = Limits(timeout_ms=1500, row_limit=10, max_bytes=100)

    adapter = _TestAdapter("ds1", _Memory(), limits)

    assert adapter.limits is limits
    assert "timeout" not in adapter.engine.get_execution_options()


def test_building_creates_no_engine():
    adapter = _TestAdapter("ds1", _Memory(), Limits())

    assert adapter._engine is None
    adapter.close()
    assert adapter._engine is None
