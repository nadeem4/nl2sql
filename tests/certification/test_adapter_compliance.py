"""
Standard Compliance Test Suite for NL2SQL Adapters.
Any new adapter MUST pass these tests to be certified.
"""
import pytest
from nl2sql.adapter_sdk import DataSourceAdapter, AdapterCapabilities

class AdapterComplianceSuite:
    @pytest.fixture
    def adapter(self) -> DataSourceAdapter:
        """Override this fixture in subclass to return the adapter under test."""
        raise NotImplementedError
        
    def test_capabilities_contract(self, adapter):
        """Verify that get_capabilities returns valid object."""
        caps = adapter.get_capabilities()
        assert isinstance(caps, AdapterCapabilities)
        # Must support at least one dialect if it supports SQL
        if caps.supports_sql:
            assert len(caps.supported_dialects) > 0

    def test_schema_contract(self, adapter):
        """Verify get_schema structure."""
        schema = adapter.get_schema()
        assert schema is not None
        assert isinstance(schema.tables, list)
        
        if len(schema.tables) > 0:
            tbl = schema.tables[0]
            assert tbl.name is not None
            assert len(tbl.columns) > 0
            assert tbl.columns[0].name is not None
            assert tbl.columns[0].data_type is not None

    def test_execution_failure_handling(self, adapter):
        """Verify that invalid queries return error object, not raise exception."""
        # Use a clearly invalid query
        res = adapter.execute("SELECT * FROM NON_EXISTENT_TABLE_XYZ_123")
        assert res.error is not None
        assert res.row_count == 0

    def test_estimation_contract(self, adapter):
        """Verify estimation returns valid object."""
        # Optional, but if implemented, must return EstimationResult
        res = adapter.estimate("SELECT 1")
        assert res is not None
        assert isinstance(res.will_succeed, bool)

class TestSqlGenericAdapter(AdapterComplianceSuite):
    """Concrete test for our SqlGenericAdapter."""
    
    @pytest.fixture
    def adapter(self):
        from nl2sql.adapters.sql_generic import SqlGenericAdapter
        # Use in-memory SQLite for testing
        return SqlGenericAdapter("sqlite:///:memory:")
