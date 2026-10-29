import json
from typing import Any, Callable, Dict, List, Literal, Optional, Tuple

from pydantic import Field, SecretStr
from sqlalchemy import URL, Connection, make_url, text
from sqlalchemy.exc import SQLAlchemyError

from nl2sql_sdk import DatasourceCapability, Estimate, ForeignKeyContract, IndexIssue, TableRef
from nl2sql_sdk.discovery import Found
from nl2sql_sdk.sqlalchemy import ReflectedTable, SQLAlchemyAdapter, SQLAlchemyConnection, driver_message

# libpq reports a failed connection before the server gives any SQLSTATE, so
# psycopg2 has none: the message is all there is. Checked in this order.
_STAGE_BY_MESSAGE = (
    ("password authentication failed", "auth"),
    ("no password supplied", "auth"),
    ("no pg_hba.conf entry", "auth"),
    ("does not exist", "database"),
    ("permission denied", "permission"),
    ("connection refused", "network"),
    ("could not translate host name", "network"),
    ("timeout expired", "network"),
    ("no route to host", "network"),
    ("could not connect", "network"),
)
# Plan nodes that read through an index: each counts its own Plan Rows.
_INDEX_SCANS = {"Index Scan", "Index Only Scan", "Bitmap Index Scan"}
# What reading an unexpected plan raises: it becomes the same RuntimeError as a failed EXPLAIN.
_UNREADABLE = (KeyError, TypeError, ValueError, AttributeError, IndexError)


class PostgresConnection(SQLAlchemyConnection):
    """A PostgreSQL datasource's ``connection`` block."""

    host: str = Field(title="Host", examples=["db.internal"])
    port: int = Field(5432, title="Port")
    database: str = Field(title="Database")
    user: str = Field(title="User")
    password: SecretStr = Field(title="Password")
    sslmode: Literal["disable", "prefer", "require", "verify-full"] = Field(
        "prefer", title="SSL mode", json_schema_extra={"group": "advanced"})


class PostgresAdapter(SQLAlchemyAdapter):
    title = "PostgreSQL"
    connection_model = PostgresConnection
    driver_modules = ("psycopg2",)
    install_hint = 'pip install "nl2sql-engine[postgres]"'
    file_suffixes = ()
    default_ports = (5432,)
    url_schemes = ("postgresql", "postgres")

    @classmethod
    def connection_for(cls, found: Found) -> Optional[Dict[str, Any]]:
        """The host and port of an open local port, or every field a ``postgresql://`` URL carries. Never connects."""
        if found.kind == "port":
            host, _, port = found.value.rpartition(":")
            return {"host": host, "port": int(port)}
        if found.kind != "url":
            return None
        url = make_url(found.value)
        fields = {"host": url.host, "port": url.port, "database": url.database, "user": url.username,
                  "password": url.password}
        return {name: value for name, value in fields.items() if value is not None}

    def url(self, connection: PostgresConnection) -> URL:
        # Name the driver: the `postgres` extra installs psycopg2, and a bare
        # `postgresql://` means psycopg (v3) from SQLAlchemy 2.1 onwards.
        # URL.create escapes the credentials, so a password holding @ : / # %
        # stays a password instead of moving the host or port boundary.
        return URL.create(
            "postgresql+psycopg2",
            username=connection.user,
            password=connection.password.get_secret_value() or None,
            host=connection.host,
            port=connection.port,
            database=connection.database,
            query={"sslmode": connection.sslmode},
        )

    def capabilities(self) -> set[DatasourceCapability]:
        return super().capabilities() | {DatasourceCapability.ENFORCES_READ_ONLY,
                                         DatasourceCapability.ENFORCES_TIMEOUT,
                                         DatasourceCapability.ESTIMATES_COST}

    def estimate(self, sql: str, parameters: Dict[str, Any]) -> Estimate:
        """The rows ``sql`` reads, from ``EXPLAIN (FORMAT JSON, VERBOSE)``, without running it.

        A ``Seq Scan`` counts its whole relation, not its ``Plan Rows``
        (what is left after the filter): the ``Plan Rows`` of an unfiltered
        scan of it, which the planner makes from ``pg_class.reltuples``
        scaled to the relation's current size on disk, so a relation never
        analyzed has one too. ``Index Scan``, ``Index Only Scan`` and
        ``Bitmap Index Scan`` count their ``Plan Rows``. A scan on the inner
        side of a ``Nested Loop`` counts once per outer row, unless a
        ``Materialize`` keeps its rows. Not counted: ``Foreign Scan``,
        ``Function Scan`` and scans under a correlated ``SubPlan`` more than
        once. ``cost`` is the plan's ``Total Cost``. Runs read-only, under
        ``statement_timeout``.
        """
        try:
            with self.engine.connect() as conn:
                plan = _plan(self._run(conn, f"EXPLAIN (FORMAT JSON, VERBOSE) {sql}", parameters).scalar())
                seq_scans, rows = scans(plan)
                sizes = {(schema, name): self._planned_size(conn, schema, name)
                         for schema, name, _ in seq_scans}
            rows += sum(sizes[(schema, name)] * times for schema, name, times in seq_scans)
            cost = plan.get("Total Cost")
            return Estimate(rows=int(rows), cost=float(cost) if cost is not None else None,
                            source="EXPLAIN (FORMAT JSON) and pg_class.reltuples")
        except SQLAlchemyError as exc:
            raise RuntimeError(f"{self} could not estimate the query: {driver_message(exc)}") from None
        except _UNREADABLE as exc:
            raise RuntimeError(f"{self} could not estimate the query: the plan could not be read "
                               f"({type(exc).__name__})") from None

    def _planned_size(self, conn: Connection, schema: str, name: str) -> int:
        """The planner's estimate of a relation's rows: ``Plan Rows`` of an unfiltered scan of it."""
        quote = conn.dialect.identifier_preparer.quote
        return int(_plan(conn.execute(text(f"EXPLAIN (FORMAT JSON) SELECT * FROM {quote(schema)}.{quote(name)}"))
                         .scalar())["Plan Rows"])

    def session_setup(self, dbapi_connection: Any) -> None:
        """Every transaction read-only, and ``limits.timeout_ms`` as the server's ``statement_timeout``.

        psycopg2 runs the SETs in a transaction, and a rollback would undo
        them, so they are committed.
        """
        statements = ["SET default_transaction_read_only = on"]
        if self.limits.timeout_ms:
            statements.append(f"SET statement_timeout = {int(self.limits.timeout_ms)}")
        cursor = dbapi_connection.cursor()
        for statement in statements:
            cursor.execute(statement)
        cursor.close()
        dbapi_connection.commit()

    def classify(self, exc: BaseException) -> Optional[str]:
        """The SQLSTATE when there is one, else libpq's message."""
        stage = super().classify(exc)
        if stage is not None:
            return stage
        message = str(getattr(exc, "orig", None) or exc).lower()
        return next((stage for words, stage in _STAGE_BY_MESSAGE if words in message), None)

    def get_dialect(self) -> str:
        # A sqlglot dialect name (SQLAlchemy calls it "postgresql").
        return "postgres"

    def system_schemas(self) -> set[str]:
        return {"pg_catalog", "information_schema"}

    def partitions(self, conn: Connection, schema: str) -> Dict[str, str]:
        """Each partition of ``schema`` by name, with the table of the same schema it directly belongs to."""
        rows = conn.execute(text(
            "SELECT child.relname, parent.relname FROM pg_catalog.pg_inherits i "
            "JOIN pg_catalog.pg_class child ON child.oid = i.inhrelid "
            "JOIN pg_catalog.pg_class parent ON parent.oid = i.inhparent "
            "JOIN pg_catalog.pg_namespace n ON n.oid = child.relnamespace "
            "WHERE n.nspname = :schema AND child.relispartition AND parent.relnamespace = child.relnamespace"
        ), {"schema": schema})
        return {child: parent for child, parent in rows}

    def reflect(self, conn: Connection, schema: str, wants_table: Callable[[str], bool],
                issues: List[IndexIssue]) -> Dict[str, ReflectedTable]:
        """The base's reflection with each partition folded into its parent (``fold_partitions``)."""
        parents = self.partitions(conn, schema)
        top = {name: _top_parent(name, parents) for name in parents}
        reflected = super().reflect(conn, schema, lambda name: wants_table(top.get(name, name)), issues)
        return fold_partitions(reflected, parents, schema)

    def row_estimates(self, conn: Connection, schema: str) -> Dict[str, int]:
        """``pg_class.reltuples``, which ANALYZE and autovacuum keep. A table never analyzed (-1) has none."""
        rows = conn.execute(text(
            "SELECT c.relname, c.reltuples FROM pg_catalog.pg_class c "
            "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :schema AND c.relkind IN ('r', 'p', 'm', 'f') AND c.reltuples >= 0"
        ), {"schema": schema})
        return add_partition_estimates({name: int(count) for name, count in rows}, self.partitions(conn, schema))


def _top_parent(name: str, parents: Dict[str, str]) -> str:
    """The table a partition belongs to once its sub-partitioning is followed; ``name`` itself when it is not one."""
    while name in parents:
        name = parents[name]
    return name


def add_partition_estimates(estimates: Dict[str, int], parents: Dict[str, str]) -> Dict[str, int]:
    """``estimates`` without the partitions; a parent with no estimate of its own (never analyzed) gets their sum."""
    summed: Dict[str, int] = {}
    for name, parent in parents.items():
        if name in estimates:
            summed[_top_parent(name, parents)] = summed.get(_top_parent(name, parents), 0) + estimates[name]
    kept = {name: count for name, count in estimates.items() if name not in parents}
    return {**{name: count for name, count in summed.items() if name not in kept}, **kept}


def fold_partitions(reflected: Dict[str, ReflectedTable], parents: Dict[str, str], schema: str) -> Dict[str, ReflectedTable]:
    """``reflected`` without its partitions, each parent holding the foreign keys its partitions declare.

    PostgreSQL declares a partitioned table's foreign keys on its partitions, so the parent, which is what a
    query names, would have none. The parent keeps the keys it declares and gains its partitions', listed once. A
    key of ``schema`` that points at a partition points at its parent.
    """
    top = {name: _top_parent(name, parents) for name in parents}

    def points_at_parent(fk: ForeignKeyContract) -> ForeignKeyContract:
        target = fk.referred_table
        if target.schema_name != schema or target.table_name not in top:
            return fk
        return fk.model_copy(update={"referred_table": TableRef(schema_name=target.schema_name,
                                                                table_name=top[target.table_name])})

    kept = {name: table for name, table in reflected.items() if name not in top}
    inherited: Dict[str, List[ForeignKeyContract]] = {}
    for name, parent in top.items():
        if name in reflected and parent in kept:
            inherited.setdefault(parent, []).extend(reflected[name].foreign_keys)
    folded = {}
    for name, table in kept.items():
        keys = [*table.foreign_keys, *inherited.get(name, [])]
        unique: Dict[Any, ForeignKeyContract] = {}
        for fk in map(points_at_parent, keys):
            unique.setdefault((tuple(fk.constrained_columns), fk.referred_table, tuple(fk.referred_columns)), fk)
        folded[name] = table._replace(foreign_keys=list(unique.values()))
    return folded


def _plan(explained: Any) -> Dict[str, Any]:
    """The top node of ``EXPLAIN (FORMAT JSON)``'s output, which psycopg2 returns parsed or as text."""
    return (json.loads(explained) if isinstance(explained, str) else explained)[0]["Plan"]


def scans(plan: Dict[str, Any]) -> Tuple[List[Tuple[str, str, float]], float]:
    """``(each Seq Scan's (schema, relation, times it runs), the rows the index scans read)``.

    A node runs once per run of its parent, and the inner side of a
    ``Nested Loop`` once per row of its outer side; below a ``Materialize``
    the rows are read once per run of the loop above it.
    """
    seq_scans: List[Tuple[str, str, float]] = []
    rows = 0.0

    def walk(node: Dict[str, Any], times: float, once: float) -> None:
        nonlocal rows
        kind = node.get("Node Type")
        if kind == "Seq Scan":
            seq_scans.append((node["Schema"], node["Relation Name"], times))
        elif kind in _INDEX_SCANS:
            rows += float(node.get("Plan Rows") or 0) * times
        children = node.get("Plans") or []
        if kind == "Nested Loop" and len(children) == 2:
            outer, inner = children
            walk(outer, times, times)
            walk(inner, times * max(1.0, float(outer.get("Plan Rows") or 1)), times)
            return
        for child in children:
            if kind == "Materialize":
                walk(child, once, once)
            else:
                walk(child, times, once)

    walk(plan, 1, 1)
    return seq_scans, rows
