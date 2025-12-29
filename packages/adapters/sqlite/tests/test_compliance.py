import pytest
from nl2sql_adapter_sdk.testing import AdapterComplianceSuite
from nl2sql_sqlite.adapter import SqliteAdapter
from sqlalchemy import text

class TestSqliteCompliance(AdapterComplianceSuite):
    @pytest.fixture
    def adapter(self):
        # In-memory database for fast testing
        adapter = SqliteAdapter("sqlite:///:memory:")
        # Seed some data for schema tests
        with adapter.engine.connect() as conn:
            conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT)"))
            conn.execute(text("INSERT INTO users (name) VALUES ('Alice')"))
            conn.commit()
        return adapter
