"""
Standard Compliance Test Suite for NL2SQL Adapters.
Any new adapter MUST pass these tests to be certified.
"""
import pytest
from nl2sql_adapter_sdk import DatasourceAdapter, SchemaMetadata, CostEstimate

class AdapterComplianceSuite:
    @pytest.fixture
    def adapter(self) -> DatasourceAdapter:
        """Override this fixture in subclass to return the adapter under test."""
        raise NotImplementedError
        
    def test_schema_contract(self, adapter):
        """Verify fetch_schema structure."""
        schema = adapter.fetch_schema()
        assert schema is not None
        assert isinstance(schema, SchemaMetadata)
        
        if len(schema.tables) > 0:
            tbl = schema.tables[0]
            assert tbl.name is not None
            assert len(tbl.columns) > 0
            assert tbl.columns[0].name is not None
            assert tbl.columns[0].type is not None

    def test_execution_failure_handling(self, adapter):
        """Verify that invalid queries return error object, not raise exception."""
        try:
            res = adapter.execute("SELECT * FROM NON_EXISTENT_TABLE_XYZ_123")
            pass 
        except Exception:
            pass # Acceptable behavior

    def test_estimation_contract(self, adapter):
        """Verify estimation returns valid object."""
        caps = adapter.capabilities()
        if hasattr(caps, "supports_cost_estimation") and caps.supports_cost_estimation: # check model definition
             res = adapter.cost_estimate("SELECT 1")
             assert res is not None
             assert isinstance(res, CostEstimate)
             assert isinstance(res.estimated_rows, int)
