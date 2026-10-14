import duckdb
import pytest
from sqlalchemy import event, text

from nl2sql_sdk import IndexScope, Limits
from nl2sql_sdk.contracts import AdapterRequest, ResultFrame
from nl2sql.adapters.duckdb.adapter import DuckdbAdapter, DuckdbConnection

from ..contract_harness import build_adapter


@pytest.fixture
def memory_adapter():
    adapter = build_adapter(
        DuckdbAdapter,
        datasource_id="duck1",
        connection={"database": ":memory:"},
    )
    # execute_sql never commits, so DDL used as test arrangement is written here.
    with adapter.engine.begin() as conn:
        conn.execute(text("CREATE TABLE t AS SELECT 1 AS x"))
    return adapter


def test_url_for_file_database(memory_adapter, tmp_path):
    # Validates URI construction because a wrong prefix silently selects another dialect.
    # Arrange
    db_path = tmp_path / "warehouse.db"

    # Act
    uri = memory_adapter.url(DuckdbConnection(database=str(db_path)))

    # Assert
    assert uri == f"duckdb:///{db_path}"


def test_url_for_in_memory_database(memory_adapter):
    # Validates the in-memory form because it is the default for ad-hoc analysis.
    # Act
    uri = memory_adapter.url(DuckdbConnection(database=":memory:"))

    # Assert
    assert uri == "duckdb:///:memory:"


def test_adapter_connects_and_reports_dialect(memory_adapter):
    # The engine is created on first use.
    # Assert
    assert memory_adapter.engine is not None
    assert memory_adapter.get_dialect() == "duckdb"


def test_execute_sql_round_trips_a_row(memory_adapter):
    # Validates real execution because a mocked engine cannot prove the driver works.
    # Act
    result = memory_adapter.execute(
        AdapterRequest(plan_type="sql", payload={"sql": "SELECT x FROM t"})
    )

    # Assert
    assert isinstance(result, ResultFrame)
    assert result.success is True
    assert result.columns == ["x"]
    assert result.rows == [[1]]
    assert result.row_count == 1


# --- DuckDB reads its own catalog -------------------------------------------

@pytest.fixture
def shop(tmp_path):
    """A DuckDB file with a primary key, a foreign key, comments, a view and DuckDB's own types."""
    path = tmp_path / "shop.duckdb"
    with duckdb.connect(str(path)) as conn:
        conn.execute("CREATE SCHEMA sales")
        conn.execute("CREATE TABLE sales.people (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL, "
                     "tags VARCHAR[], doc JSON)")
        conn.execute("CREATE TABLE sales.orders (id INTEGER PRIMARY KEY, "
                     "person_id INTEGER REFERENCES sales.people(id), amount DECIMAL(10,2))")
        conn.execute("CREATE VIEW sales.big_orders AS SELECT id, amount FROM sales.orders WHERE amount > 10")
        conn.execute("COMMENT ON TABLE sales.orders IS 'One row per order'")
        conn.execute("COMMENT ON COLUMN sales.orders.amount IS 'In euros'")
        conn.execute("INSERT INTO sales.people VALUES (1, 'Ada', ['x'], '{}')")
        conn.execute("INSERT INTO sales.orders VALUES (1, 1, 12.50)")
    adapter = DuckdbAdapter("shop", DuckdbConnection(database=str(path)), Limits())
    yield adapter
    adapter.close()


def _statements(adapter):
    seen = []
    event.listen(adapter.engine, "before_cursor_execute",
                 lambda conn, cursor, statement, *args: seen.append(statement))
    return seen


def test_schema_reading_never_queries_pg_catalog(shop):
    # duckdb-engine reflects with SQLAlchemy's PostgreSQL reflection, which
    # from SQLAlchemy 2.1 queries pg_catalog.pg_collation, a table DuckDB lacks.
    seen = _statements(shop)

    assert shop.test_connection().ok
    shop.fetch_schema_snapshot(IndexScope())

    assert seen
    assert not [s for s in seen if "pg_catalog" in s.lower()]


def test_schema_reading_keeps_keys_comments_views_and_duckdb_types(shop):
    snapshot = shop.fetch_schema_snapshot(IndexScope(profile="catalog"))

    tables = snapshot.contract.tables
    people = next(t for t in tables.values() if t.table.table_name == "people")
    orders = next(t for t in tables.values() if t.table.table_name == "orders")
    assert {t.table.table_name for t in tables.values()} == {"people", "orders", "big_orders"}
    assert orders.table.schema_name == "shop.sales"
    assert orders.columns["id"].is_primary_key and not orders.columns["person_id"].is_primary_key
    assert not people.columns["name"].is_nullable and people.columns["tags"].is_nullable
    assert (people.columns["tags"].data_type, people.columns["doc"].data_type,
            orders.columns["amount"].data_type) == ("VARCHAR[]", "JSON", "DECIMAL(10,2)")
    [fk] = orders.foreign_keys
    assert (fk.constrained_columns, fk.referred_table.full_name, fk.referred_columns) == (
        ["person_id"], people.table.full_name, ["id"])
    meta = next(m for m in snapshot.metadata.tables.values() if m.table.table_name == "orders")
    assert meta.description == "One row per order"
    assert meta.columns["amount"].description == "In euros"
    assert not snapshot.issues
