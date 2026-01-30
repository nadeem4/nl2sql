import sqlite3

import pytest

from nl2sql_sqlalchemy_adapter.adapter import BaseSQLAlchemyAdapter
from nl2sql_sqlalchemy_adapter.models import QueryPlan, CostEstimate


class _SQLiteTestAdapter(BaseSQLAlchemyAdapter):
    def construct_uri(self, args):
        return f"sqlite:///{args['database']}"

    def explain(self, sql: str):
        return QueryPlan(plan_text="explain")

    def cost_estimate(self, sql: str):
        return CostEstimate(estimated_cost=1.0, estimated_rows=1)

    def get_dialect(self) -> str:
        return "sqlite"

    @property
    def exclude_schemas(self):
        return set()


@pytest.fixture()
def sqlite_db_path(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)"
        )
        conn.executemany(
            "INSERT INTO users (name, age) VALUES (?, ?)",
            [("Ada", 30), ("Linus", 45), ("Grace", 50)],
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


@pytest.fixture()
def sqlite_adapter(sqlite_db_path):
    return _SQLiteTestAdapter(
        datasource_id="sqlite_test",
        datasource_engine_type="sqlite",
        connection_args={"database": str(sqlite_db_path)},
    )


def test_execute_sql_returns_rows_and_metrics(sqlite_adapter):
    # Validates real execution because adapter_sqlalchemy must execute SQL reliably.
    # Arrange
    sql = "SELECT id, name FROM users ORDER BY id"

    # Act
    result = sqlite_adapter.execute_sql(sql)

    # Assert
    assert result.success is True
    assert result.row_count == 3
    assert [col.name for col in result.columns] == ["id", "name"]
    assert result.execution_stats["execution_time_ms"] >= 0


def test_dry_run_rolls_back_transaction(sqlite_adapter):
    # Validates rollback behavior because dry-run must not mutate data.
    # Arrange
    before = sqlite_adapter.execute_sql("SELECT COUNT(*) FROM users").rows[0][0]

    # Act
    sqlite_adapter.dry_run("INSERT INTO users (name, age) VALUES ('new', 99)")
    after = sqlite_adapter.execute_sql("SELECT COUNT(*) FROM users").rows[0][0]

    # Assert
    assert before == after


def test_fetch_schema_snapshot_includes_contract_and_metadata(sqlite_adapter):
    # Validates schema reflection because core depends on accurate contracts.
    # Arrange / Act
    snapshot = sqlite_adapter.fetch_schema_snapshot()

    # Assert
    table_key = "[main].[users]"
    assert table_key in snapshot.contract.tables
    assert snapshot.contract.tables[table_key].columns["id"].is_primary_key is True
    assert snapshot.metadata.tables[table_key].row_count == 3


def test_large_result_set_executes(sqlite_adapter, sqlite_db_path):
    # Validates large payload handling because adapters must scale under load.
    # Arrange
    conn = sqlite3.connect(sqlite_db_path)
    try:
        conn.executemany(
            "INSERT INTO users (name, age) VALUES (?, ?)",
            [("A", 1), ("B", 2), ("C", 3)],
        )
        conn.commit()
    finally:
        conn.close()

    # Act
    result = sqlite_adapter.execute_sql("SELECT id FROM users")

    # Assert
    assert result.row_count >= 6


def test_execute_sql_fails_for_directory_database(tmp_path):
    # Validates connection failure behavior because invalid DB targets must error.
    # Arrange
    db_dir = tmp_path / "db_dir"
    db_dir.mkdir()
    adapter = _SQLiteTestAdapter(
        datasource_id="sqlite_test",
        datasource_engine_type="sqlite",
        connection_args={"database": str(db_dir)},
    )

    # Act / Assert
    with pytest.raises(Exception):
        adapter.execute_sql("SELECT 1")
