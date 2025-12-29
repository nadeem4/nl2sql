import pytest
from nl2sql_adapter_sdk.testing import AdapterComplianceSuite
from nl2sql_mssql.adapter import MssqlAdapter
import os

# Skip if no MSSQL connection string provided
MSSQL_CONN = os.environ.get("MSSQL_CONNECTION_STRING")

@pytest.mark.skipif(not MSSQL_CONN, reason="MSSQL_CONNECTION_STRING not set")
class TestMssqlCompliance(AdapterComplianceSuite):
    @pytest.fixture
    def adapter(self):
        return MssqlAdapter(MSSQL_CONN)
