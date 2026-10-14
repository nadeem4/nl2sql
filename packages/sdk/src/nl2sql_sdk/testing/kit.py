"""``AdapterConformance``: the checks every adapter must pass, as a test class to subclass.

An adapter author subclasses it in a test module and provides four fixtures:

| Fixture | What it gives |
|---|---|
| ``connection`` | The ``ConnectionModel`` of a database the tests may seed |
| ``adapter_factory`` | ``adapter_factory(connection, limits)``: the adapter built for them, as the engine builds it |
| ``seed`` | ``seed(statements)``: runs each SQL statement with a *writable* connection, outside the adapter |
| ``sleep_sql`` | ``sleep_sql(seconds)``: a query that runs at least that many seconds |

and, for a database with logins, ``limited_login``: the ``ConnectionModel``
of a second login whose password holds ``@ : / # %``, that can read the
seed's ``kit_people`` but not its ``kit_orders``. It may use the ``seeded``
fixture to grant on the seed. Without it, the checks that need it are
skipped with the reason.

The kit's seed (two schemas, a view, a mixed-case table, boolean, uuid and
json columns) is written once and transpiled to ``get_dialect()`` with
sqlglot. A database with one schema sets ``schemas = ("main",)``: the seed
then creates no schema, and the checks that need two are skipped. A database
without one of the column types maps it to its nearest in ``column_types``.

Each test seeds the database first and drops the seed after. A capability the
adapter does not declare is not checked: its test is reported as skipped,
with the reason.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import pytest
import sqlglot
from pydantic import BaseModel, SecretStr, ValidationError

from nl2sql_sdk.capabilities import DatasourceCapability
from nl2sql_sdk.contracts import AdapterRequest, ResultFrame
from nl2sql_sdk.models import ConnectionModel, IndexScope, Limits
from nl2sql_sdk.schema import SchemaSnapshot

# The seed is written in this dialect and transpiled to the adapter's.
SOURCE_DIALECT = "postgres"

# Schema names no adapter may index, whatever its scope says. Matched on the
# last part of a dotted name, ignoring case.
SYSTEM_SCHEMAS = ("information_schema", "pg_catalog", "pg_toast", "mysql", "performance_schema", "sys")

PASSWORD_CHARACTERS = "@:/#%"
MISSING_DATABASE = "nl2sql_kit_missing_db"
INJECTION = "1; DROP TABLE kit_people"
_PARAMETER = "'__nl2sql_kit_parameter__'"


def _sql(sql: str) -> AdapterRequest:
    return AdapterRequest(plan_type="sql", payload={"sql": sql})


def _short(schema: str) -> str:
    """A schema's own name: DuckDB lists schemas as ``catalog.schema``."""
    return schema.rsplit(".", 1)[-1]


def _tables(snapshot: SchemaSnapshot) -> Dict[Tuple[str, str], Any]:
    """``{(schema, table): TableMetadata}``, with the schema's own name."""
    return {(_short(m.table.schema_name), m.table.table_name): m for m in snapshot.metadata.tables.values()}


def _secrets(model: BaseModel) -> List[str]:
    return [value.get_secret_value() for value in model.__dict__.values()
            if isinstance(value, SecretStr) and value.get_secret_value()]


def _with(connection: ConnectionModel, **changes: Any) -> ConnectionModel:
    """``connection`` with ``changes``, validated again (``model_copy`` would skip validation)."""
    return type(connection).model_validate({**dict(connection), **changes})


class AdapterConformance:
    """The conformance kit. Subclass it as ``Test<Adapter>Conformance`` and provide its fixtures."""

    # The seed's two schemas, created and dropped by the seed. One name: the
    # database's only schema, which the seed neither creates nor drops.
    schemas: Tuple[str, ...] = ("kit_a", "kit_b")
    # The seed's column types, in PostgreSQL's SQL. Override one the database lacks.
    column_types: Dict[str, str] = {"boolean": "BOOLEAN", "uuid": "UUID", "json": "JSON"}

    # --- What the author provides --------------------------------------------

    @pytest.fixture
    def connection(self) -> ConnectionModel:
        raise NotImplementedError("Provide a `connection` fixture: the ConnectionModel of a database to seed")

    @pytest.fixture
    def adapter_factory(self) -> Callable[[ConnectionModel, Limits], Any]:
        raise NotImplementedError("Provide an `adapter_factory` fixture: (connection, limits) -> adapter")

    @pytest.fixture
    def seed(self) -> Callable[[List[str]], None]:
        raise NotImplementedError("Provide a `seed` fixture: runs SQL statements with a writable connection")

    @pytest.fixture
    def sleep_sql(self) -> Callable[[int], str]:
        raise NotImplementedError("Provide a `sleep_sql` fixture: (seconds) -> a query that runs that long")

    @pytest.fixture
    def limited_login(self) -> Optional[ConnectionModel]:
        return None

    # --- The kit's own fixtures ----------------------------------------------

    @property
    def _a(self) -> str:
        return self.schemas[0]

    @property
    def _b(self) -> str:
        return self.schemas[-1]

    def _scope(self, **fields: Any) -> IndexScope:
        """A scope over the seed's schemas only, so a check never reads the rest of the database."""
        patterns = [p for s in set(self.schemas) for p in (s, f"*.{s}")]
        return IndexScope(include_schemas=fields.pop("include_schemas", patterns), **fields)

    def _seed_sql(self) -> Tuple[List[str], List[str]]:
        """``(statements that drop the seed, statements that make it)``, in ``SOURCE_DIALECT``."""
        a, b = self._a, self._b
        types = {**AdapterConformance.column_types, **self.column_types}
        owns_schemas = len(self.schemas) > 1
        drop = [
            f"DROP VIEW IF EXISTS {a}.kit_active_people",
            f"DROP TABLE IF EXISTS {a}.kit_made",
            f"DROP TABLE IF EXISTS {a}.kit_people",
            f'DROP TABLE IF EXISTS {a}."KitMixedCase"',
            f"DROP TABLE IF EXISTS {b}.kit_orders",
        ] + ([f"DROP SCHEMA IF EXISTS {b}", f"DROP SCHEMA IF EXISTS {a}"] if owns_schemas else [])
        make = ([f"CREATE SCHEMA {a}", f"CREATE SCHEMA {b}"] if owns_schemas else []) + [
            f"CREATE TABLE {a}.kit_people (id INT PRIMARY KEY, name VARCHAR(50), "
            f"active {types['boolean']}, token {types['uuid']}, doc {types['json']})",
            f'CREATE TABLE {a}."KitMixedCase" (id INT PRIMARY KEY, "DisplayName" VARCHAR(50))',
            f"CREATE VIEW {a}.kit_active_people AS SELECT id, name FROM {a}.kit_people WHERE active = TRUE",
            f"CREATE TABLE {b}.kit_orders (id INT PRIMARY KEY, person_id INT, amount DECIMAL(10, 2))",
            f"INSERT INTO {a}.kit_people (id, name, active, token, doc) VALUES "
            f"(1, 'Ada', TRUE, 'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11', '{{\"a\": 1}}'), "
            f"(2, 'Grace', FALSE, 'b1ffcd88-8d1a-4ef9-ac7e-7cc0ce491b22', '{{\"b\": 2}}')",
            f'INSERT INTO {a}."KitMixedCase" (id, "DisplayName") VALUES (1, \'Mixed\')',
            f"INSERT INTO {b}.kit_orders (id, person_id, amount) VALUES (1, 1, 9.50), (2, 2, 12.00)",
        ]
        return drop, make

    @pytest.fixture
    def dialect(self, connection, adapter_factory) -> str:
        adapter = adapter_factory(connection, Limits())
        try:
            return adapter.get_dialect()
        finally:
            adapter.close()

    @pytest.fixture
    def seeded(self, seed, dialect) -> Iterator[None]:
        """The kit's seed in the database, dropped after the test."""
        drop, make = self._seed_sql()
        seed(self._to(dialect, drop + make))
        yield
        seed(self._to(dialect, drop))

    @pytest.fixture
    def build(self, seeded, connection, adapter_factory) -> Iterator[Callable[..., Any]]:
        """``build(connection=None, limits=None)``: an adapter on the seeded database, closed after the test."""
        built = []

        def make(on: Optional[ConnectionModel] = None, limits: Optional[Limits] = None):
            adapter = adapter_factory(on if on is not None else connection, limits or Limits())
            built.append(adapter)
            return adapter

        yield make
        for adapter in built:
            adapter.close()

    @staticmethod
    def _to(dialect: str, statements: List[str]) -> List[str]:
        return [sqlglot.transpile(s, read=SOURCE_DIALECT, write=dialect)[0] for s in statements]

    def _query(self, adapter: Any, sql: str) -> str:
        """``sql``, written against the seed in ``SOURCE_DIALECT``, as the adapter's SQL."""
        return self._to(adapter.get_dialect(), [sql.format(a=self._a, b=self._b)])[0]

    def _run(self, adapter: Any, sql: str) -> ResultFrame:
        return adapter.execute(_sql(self._query(adapter, sql)))

    def _count(self, adapter: Any, table: str) -> int:
        result = self._run(adapter, f"SELECT COUNT(*) AS n FROM {table}")
        assert result.success, result.error
        return int(result.rows[0][0])

    def _needs_two_schemas(self) -> None:
        if len(self.schemas) < 2:
            pytest.skip(f"this database has one schema ({self._a}): nothing to tell two schemas apart")

    def _needs(self, connection: ConnectionModel, field: str) -> None:
        if field not in type(connection).model_fields:
            pytest.skip(f"{type(connection).__name__} has no `{field}`")

    # --- Dialect -------------------------------------------------------------

    def test_dialect_is_a_sqlglot_dialect(self, connection, adapter_factory):
        adapter = adapter_factory(connection, Limits())
        try:
            sqlglot.Dialect.get_or_raise(adapter.get_dialect())
        finally:
            adapter.close()

    # --- Connection model ----------------------------------------------------

    def test_unknown_connection_key_fails(self, connection):
        with pytest.raises(ValidationError):
            _with(connection, nl2sql_kit_unknown_key="x")

    def test_no_secret_in_repr_str_or_error(self, build, connection):
        secrets = _secrets(connection)
        if not secrets:
            pytest.skip(f"{type(connection).__name__} holds no secret")
        with pytest.raises(ValidationError) as invalid:
            _with(connection, nl2sql_kit_unknown_key="x")
        check = build(_with(connection, **({"database": MISSING_DATABASE}
                                           if "database" in type(connection).model_fields else {}))
                      ).test_connection()
        for secret in secrets:
            for shown in (repr(connection), str(connection), str(invalid.value), check.message or "", check.fix or ""):
                assert secret not in shown

    def test_password_with_url_characters_connects(self, build, limited_login):
        if limited_login is None:
            pytest.skip("no `limited_login` fixture: a login whose password holds @ : / # %")
        assert any(all(c in s for c in PASSWORD_CHARACTERS) for s in _secrets(limited_login)), (
            "limited_login's password must hold @ : / # %")
        check = build(limited_login).test_connection()
        assert check.ok, check.message

    # --- Stages --------------------------------------------------------------

    def test_good_settings_are_ok(self, build):
        check = build().test_connection()
        assert (check.ok, check.stage) == (True, None), check.message

    def test_wrong_password_is_auth(self, build, connection):
        self._needs(connection, "password")
        check = build(_with(connection, password="nl2sql-kit-wrong")).test_connection()
        assert (check.ok, check.stage) == (False, "auth"), check.message
        assert check.fix

    def test_closed_port_is_network(self, build, connection):
        self._needs(connection, "port")
        check = build(_with(connection, port=1)).test_connection()
        assert (check.ok, check.stage) == (False, "network"), check.message
        assert check.fix

    def test_missing_database_is_database(self, build, connection):
        self._needs(connection, "database")
        check = build(_with(connection, database=MISSING_DATABASE)).test_connection()
        assert (check.ok, check.stage) == (False, "database"), check.message
        assert check.fix

    # --- Scope ---------------------------------------------------------------

    def test_system_schemas_are_left_out(self, build):
        """Even a scope that names them indexes no system schema."""
        named = [p for s in SYSTEM_SCHEMAS for p in (s, f"*.{s}")] + [self._a, f"*.{self._a}"]
        snapshot = build().fetch_schema_snapshot(self._scope(include_schemas=named, profile="catalog"))
        schemas = {schema.lower() for schema, _ in _tables(snapshot)}
        assert schemas == {self._a.lower()}

    def test_include_and_exclude_rules_hold(self, build):
        adapter = build()
        snapshot = adapter.fetch_schema_snapshot(self._scope(exclude_tables=["*.kit_orders"], profile="catalog"))
        tables = {table for _, table in _tables(snapshot)}
        assert "kit_people" in tables and "kit_orders" not in tables

        snapshot = adapter.fetch_schema_snapshot(
            self._scope(include_tables=["*.kit_people"], exclude_columns=["*.kit_people.doc"], profile="catalog"))
        assert {table for _, table in _tables(snapshot)} == {"kit_people"}
        assert "doc" not in next(iter(snapshot.contract.tables.values())).columns

    def test_exclude_schemas_holds(self, build):
        self._needs_two_schemas()
        snapshot = build().fetch_schema_snapshot(
            self._scope(exclude_schemas=[self._b, f"*.{self._b}"], profile="catalog"))
        assert {schema for schema, _ in _tables(snapshot)} == {self._a}

    def test_views_are_included(self, build):
        snapshot = build().fetch_schema_snapshot(self._scope(profile="catalog"))
        assert (self._a, "kit_active_people") in _tables(snapshot)

    def test_mixed_case_names_keep_their_case(self, build):
        snapshot = build().fetch_schema_snapshot(self._scope(profile="catalog"))
        assert (self._a, "KitMixedCase") in _tables(snapshot)
        contract = next(c for c in snapshot.contract.tables.values() if c.table.table_name == "KitMixedCase")
        assert "DisplayName" in contract.columns

    # --- Odd columns and row estimates ---------------------------------------

    def test_odd_columns_profile_or_report_an_issue(self, build):
        snapshot = build().fetch_schema_snapshot(self._scope(include_tables=["*.kit_people"], sample_values=True))
        people = _tables(snapshot)[(self._a, "kit_people")]
        for name in ("active", "token", "doc"):
            reported = any(issue.column == name for issue in snapshot.issues)
            assert people.columns[name].statistics is not None or reported, name

    def test_every_table_has_a_row_count_and_its_source(self, build):
        snapshot = build().fetch_schema_snapshot(self._scope())
        tables = _tables(snapshot)
        assert tables
        for key, metadata in tables.items():
            assert metadata.row_count is not None, key
            assert metadata.row_count_source in ("catalog", "exact"), key

    # --- Execution -----------------------------------------------------------

    def test_bound_parameter_is_a_value(self, build):
        adapter = build()
        if DatasourceCapability.SUPPORTS_BOUND_PARAMETERS not in adapter.capabilities():
            pytest.skip(f"{type(adapter).__name__} does not declare SUPPORTS_BOUND_PARAMETERS")
        sql = self._query(adapter, f"SELECT COUNT(*) AS n FROM {{a}}.kit_people WHERE name = {_PARAMETER}")
        assert _PARAMETER in sql
        result = adapter.execute(AdapterRequest(plan_type="sql", payload={"sql": sql.replace(_PARAMETER, ":name")},
                                                parameters={"name": INJECTION}))
        assert result.success, result.error
        assert int(result.rows[0][0]) == 0
        assert self._count(adapter, "{a}.kit_people") == 2

    def test_writes_fail_when_read_only_is_declared(self, build):
        adapter = build()
        if DatasourceCapability.ENFORCES_READ_ONLY not in adapter.capabilities():
            pytest.skip(f"{type(adapter).__name__} does not declare ENFORCES_READ_ONLY: "
                        "the login's grants are the only guard against a write")
        inserted = self._run(adapter, "INSERT INTO {a}.kit_people (id, name) VALUES (99, 'kit')")
        created = self._run(adapter, "CREATE TABLE {a}.kit_made (id INT)")

        assert inserted.success is False and inserted.error.retryable is False
        assert created.success is False
        assert self._count(adapter, "{a}.kit_people") == 2
        assert self._run(adapter, "SELECT COUNT(*) AS n FROM {a}.kit_made").success is False

    def test_slow_query_is_stopped_when_timeout_is_declared(self, build, sleep_sql):
        adapter = build(limits=Limits(timeout_ms=1000))
        if DatasourceCapability.ENFORCES_TIMEOUT not in adapter.capabilities():
            pytest.skip(f"{type(adapter).__name__} does not declare ENFORCES_TIMEOUT")
        start = time.perf_counter()
        result = adapter.execute(_sql(sleep_sql(5)))
        elapsed = time.perf_counter() - start

        assert result.success is False
        assert elapsed < 3, f"stopped after {elapsed:.1f}s"
        assert result.error.error_code == "QUERY_TIMEOUT", result.error
        assert result.error.retryable is False

    def test_missing_column_is_retryable(self, build):
        result = self._run(build(), "SELECT kit_missing_column FROM {a}.kit_people")
        assert result.success is False
        assert result.error.retryable is True, result.error

    def test_permission_error_is_not_retryable(self, build, limited_login):
        if limited_login is None:
            pytest.skip("no `limited_login` fixture: a login that cannot read kit_orders")
        adapter = build(limited_login)
        assert self._count(adapter, "{a}.kit_people") == 2
        result = self._run(adapter, "SELECT id FROM {b}.kit_orders")
        assert result.success is False
        assert result.error.retryable is False, result.error

    def test_streamed_frames_match_execute(self, build):
        adapter = build()
        if not hasattr(adapter, "execute_batches"):
            pytest.skip(f"{type(adapter).__name__} has no execute_batches")
        request = _sql(self._query(adapter, "SELECT id, name FROM {a}.kit_people ORDER BY id"))
        whole = adapter.execute(request)
        frames = list(adapter.execute_batches(request, 1))

        assert whole.success and all(frame.success for frame in frames)
        assert all(frame.columns == whole.columns for frame in frames)
        assert [row for frame in frames for row in frame.rows] == whole.rows
