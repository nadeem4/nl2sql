import pytest
from nl2sql_adapter_sdk.testing import AdapterComplianceSuite
from nl2sql_mysql.adapter import MysqlAdapter
import os

MSSQL_CONN = os.environ.get("MYSQL_CONNECTION_STRING")

@pytest.mark.skipif(not MSSQL_CONN, reason="MYSQL_CONNECTION_STRING not set")
class TestMysqlCompliance(AdapterComplianceSuite):
    @pytest.fixture
    def adapter(self):
        return MysqlAdapter(MSSQL_CONN)
