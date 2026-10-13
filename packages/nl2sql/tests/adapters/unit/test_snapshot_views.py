"""A schema snapshot includes views, not only tables.

Preferred tables are usually views a data team built, so a snapshot that read
only ``get_table_names()`` left them out of the index and the planner never
saw them. Every built-in adapter snapshots through the SQLAlchemy base, which
now reads views as well.
"""
import sqlite3

from sqlalchemy import inspect, text

from nl2sql.adapters.duckdb.adapter import DuckdbAdapter
from nl2sql.adapters.sqlite.adapter import SqliteAdapter

from ..contract_harness import build_adapter


def _names(snapshot):
    return sorted(c.table.table_name for c in snapshot.contract.tables.values())


def test_a_sqlite_snapshot_includes_views(tmp_path):
    path = tmp_path / "views.sqlite"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, customer TEXT, total REAL)")
        con.execute("INSERT INTO invoices VALUES (1, 'a', 2.5), (2, 'b', 4.0)")
        con.execute(
            "CREATE VIEW v_customer_revenue AS SELECT customer, SUM(total) AS revenue FROM invoices GROUP BY customer"
        )
    adapter = SqliteAdapter(datasource_id="t", datasource_engine_type="sqlite",
                            connection_args={"type": "sqlite", "database": path.as_posix()})
    adapter.connect()

    snapshot = adapter.fetch_schema_snapshot()

    assert _names(snapshot) == ["invoices", "v_customer_revenue"]
    [view] = [c for c in snapshot.contract.tables.values() if c.table.table_name == "v_customer_revenue"]
    assert sorted(view.columns) == ["customer", "revenue"]
    [meta] = [m for m in snapshot.metadata.tables.values() if m.table.table_name == "v_customer_revenue"]
    assert meta.row_count == 2


def test_duckdb_lists_views_with_its_tables(tmp_path):
    # Only the listing: duckdb 1.5.6 with duckdb-engine 0.17 cannot reflect a
    # file database's columns at all (pg_catalog.pg_collation is missing), for
    # tables as much as views, so a full snapshot is not what this checks.
    adapter = build_adapter(DuckdbAdapter, datasource_id="duck1", engine_type="duckdb",
                            connection_args={"type": "duckdb", "database": str(tmp_path / "views.duckdb")})
    with adapter.engine.begin() as conn:
        conn.execute(text("CREATE TABLE invoices AS SELECT 1 AS id, 2.5 AS total"))
        conn.execute(text("CREATE VIEW v_revenue AS SELECT SUM(total) AS revenue FROM invoices"))
    inspector = inspect(adapter.engine)
    excluded = {s.lower() for s in adapter.exclude_schemas}

    names = {
        name
        for schema in inspector.get_schema_names() if schema.lower() not in excluded
        for name in adapter._relation_names(inspector, schema)
    }

    assert names == {"invoices", "v_revenue"}
