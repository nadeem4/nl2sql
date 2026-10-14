"""``preferred_tables`` on a datasource: the config, the registry, and the warning.

A preferred table missing from the indexed schema is a warning, not a failure:
the retriever simply has nothing to rank first, and the usual cause (a view
created after the last index) is fixed by re-indexing.
"""
from __future__ import annotations

import pytest
from index_helpers import Chunk, TestIndex  # same directory
from langchain_core.embeddings import FakeEmbeddings
from pydantic import ValidationError

from nl2sql.config import Datasource, Limits
from nl2sql.datasources.models import matches_table
from nl2sql.datasources.registry import DatasourceRegistry
from nl2sql.indexing.health import index_health, preferred_table_warnings
from nl2sql.secrets.manager import SecretManager
from nl2sql.testing import adapter_registry
from nl2sql_sdk.schema import (
    SchemaContract,
    SchemaMetadata,
    SchemaSnapshot,
    TableContract,
    TableRef,
)


def _config(**extra):
    return Datasource(id="ds1", type="stub", **extra)


def test_preferred_tables_default_to_empty():
    assert _config().preferred_tables == []


def test_preferred_tables_are_read_from_yaml_shape():
    config = Datasource.model_validate({
        "id": "ds1", "type": "stub",
        "preferred_tables": ["v_customer_revenue", "sales.v_monthly"],
    })

    assert config.preferred_tables == ["v_customer_revenue", "sales.v_monthly"]


def test_a_blank_preferred_table_is_refused():
    with pytest.raises(ValidationError, match="preferred_tables"):
        _config(preferred_tables=["v_customer_revenue", "  "])


@pytest.mark.parametrize("name", ["v_rev", "V_REV", "main.v_rev", "[main].[v_rev]"])
def test_a_preferred_name_matches_with_or_without_its_schema(name):
    assert matches_table(name, TableRef(schema_name="main", table_name="v_rev"))


def test_a_preferred_name_does_not_match_another_schema():
    assert not matches_table("sales.v_rev", TableRef(schema_name="main", table_name="v_rev"))


class _StubAdapter:
    interface_version = 1
    driver_modules = ()
    install_hint = ""

    def __init__(self, datasource_id, datasource_engine_type, connection_args, **kwargs):
        pass

    def capabilities(self):
        return set()


def test_the_registry_keeps_each_datasources_preferred_tables(monkeypatch):
    registry = DatasourceRegistry(SecretManager(), adapter_registry({"stub": _StubAdapter}))

    registry.register_datasource(_config(preferred_tables=["v_rev"]))

    assert registry.get_preferred_tables("ds1") == ["v_rev"]
    assert registry.get_preferred_tables("unknown") == []


def _snapshot(*names):
    tables = {}
    for name in names:
        ref = TableRef(schema_name="main", table_name=name)
        tables[ref.full_name] = TableContract(table=ref, columns={}, foreign_keys=[])
    return SchemaSnapshot(
        contract=SchemaContract(datasource_id="ds1", engine_type="sqlite", tables=tables),
        metadata=SchemaMetadata(datasource_id="ds1", engine_type="sqlite", tables={}),
    )


def test_a_preferred_table_missing_from_the_snapshot_is_a_warning():
    warnings = preferred_table_warnings(
        {"ds1": ["invoices", "v_missing"]}, lambda _ds: _snapshot("invoices")
    )

    assert len(warnings) == 1
    assert "v_missing" in warnings[0] and "ds1" in warnings[0]
    assert "nl2sql index" in warnings[0]


def test_an_unindexed_datasource_gives_no_preferred_warning():
    # No snapshot is already a problem the health check reports on its own.
    assert preferred_table_warnings({"ds1": ["v_missing"]}, lambda _ds: None) == []


class _SchemaStore:
    def get_snapshot(self, _ds, _version):
        return _snapshot("invoices")


def test_index_health_carries_the_warning_and_stays_ok(tmp_path):
    index = TestIndex(tmp_path, FakeEmbeddings(size=8))
    index.write("ds1", [Chunk("datasource ds1", type="schema.datasource", datasource_id="ds1")])

    health = index_health(index.index, index.namespace, _SchemaStore(), ["ds1"],
                          preferred_tables={"ds1": ["v_missing"]})

    assert health.ok
    assert any("v_missing" in w for w in health.warnings)
