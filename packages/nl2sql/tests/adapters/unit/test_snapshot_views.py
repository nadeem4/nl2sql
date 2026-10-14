"""A schema snapshot includes views, not only tables.

Preferred tables are usually views a data team built, so a snapshot that read
only ``get_table_names()`` left them out of the index and the planner never
saw them. Every built-in adapter snapshots through the SQLAlchemy base, which
now reads views as well.
"""
import sqlite3

from nl2sql.adapters.sqlite.adapter import SqliteAdapter, SqliteConnection
from nl2sql_sdk import IndexScope, Limits


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
    adapter = SqliteAdapter("t", SqliteConnection(database=path.as_posix()), Limits())

    snapshot = adapter.fetch_schema_snapshot(IndexScope())

    assert _names(snapshot) == ["invoices", "v_customer_revenue"]
    [view] = [c for c in snapshot.contract.tables.values() if c.table.table_name == "v_customer_revenue"]
    assert sorted(view.columns) == ["customer", "revenue"]
    [meta] = [m for m in snapshot.metadata.tables.values() if m.table.table_name == "v_customer_revenue"]
    assert meta.row_count == 2
