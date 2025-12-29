import pytest
from nl2sql_adapter_sdk.testing import AdapterComplianceSuite
from nl2sql_postgres.adapter import PostgresAdapter
import os

POSTGRES_CONN = os.environ.get("POSTGRES_CONNECTION_STRING")

@pytest.mark.skipif(not POSTGRES_CONN, reason="POSTGRES_CONNECTION_STRING not set")
class TestPostgresCompliance(AdapterComplianceSuite):
    @pytest.fixture
    def adapter(self):
        adapter = PostgresAdapter()
        adapter.connect({"connection_string": POSTGRES_CONN})
        return adapter
