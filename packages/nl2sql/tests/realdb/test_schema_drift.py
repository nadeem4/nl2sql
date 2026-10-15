"""Schema drift on a real PostgreSQL: a column added after the build shows in ``engine.index.check`` (D1).

docs/architecture/wave_a.md, section 10. The table lives in its own schema
of the sample database, made and dropped by the test with the admin login,
and the datasource indexes only that schema, so the build is small. The
embedder is a fake: nothing is downloaded and no model is called.
"""
from __future__ import annotations

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from sqlalchemy import text

from nl2sql import NL2SQL, Embeddings, UserContext
from nl2sql.testing.config import make_config

from .servers import datasource

SCHEMA = "nl2sql_d1_drift"


@pytest.fixture
def probe_schema(server):
    if server.name != "postgres":
        pytest.skip("the drift check is proven on PostgreSQL; the diff itself is the engine's, not the adapter's")
    admin = server.engine()
    with admin.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        conn.execute(text(f"CREATE TABLE {SCHEMA}.orders (id INTEGER PRIMARY KEY, total NUMERIC)"))
    yield admin
    with admin.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
    admin.dispose()


def test_check_sees_an_added_column(server, probe_schema, tmp_path):
    source = datasource(server, index={"include_schemas": [SCHEMA]}).model_dump(mode="json", exclude_defaults=True)
    config = make_config(tmp_path, datasources=[source],
                         llm={"provider": "openai", "model": "gpt-test", "api_key": "sk-fake-realdb"})
    embedder = DeterministicFakeEmbedding(size=8)
    admin = UserContext(roles=["admin"])
    with NL2SQL(config.model_copy(update={"embeddings": Embeddings(instance=embedder, dimension=8)})) as engine:
        assert engine.index.build().ok
        [before] = engine.index.check(user_context=admin)
        assert before.status == "unchanged", before

        with probe_schema.begin() as conn:
            conn.execute(text(f"ALTER TABLE {SCHEMA}.orders ADD COLUMN placed_at TIMESTAMP"))
        [after] = engine.index.check(user_context=admin)

        assert after.status == "drifted", after
        assert [(c.table.schema_name, c.table.table_name, c.column_name) for c in after.diff.columns_added] == \
            [(SCHEMA, "orders", "placed_at")]
        assert after.diff.is_empty() is False and not after.diff.tables_added
        # Measured, not acted on: the build is the one made before the column existed.
        assert engine.index.diff(source["id"], user_context=admin) is None
