"""Write the test bed's generated wide schema as one SQL file for one server.

    python testbed/wide_schema.py --dialect postgres|mysql|tsql --seed 7 [--out FILE]

The same seed always gives the same schema: 20 schemas (``sales_01`` ...
``ops_20``) of 60 tables each, 1,200 tables with 5 to 30 columns, plus a small
``audit`` schema; two views per schema; boolean, uuid, json, binary, xml and
array columns where the server has the type; comments on about 30% of tables
and columns; foreign keys within each schema; 10 mixed-case table names; and
3 tables of 1,000,000 rows and 50 of 1,000, filled on the server so the file
stays small. The rest are empty.

On MySQL a schema is a database, so ``sales_01`` is written as ``wide_s01`` and
``audit`` as ``wide_audit``. On PostgreSQL and SQL Server the file is run inside
a database called ``wide``, which the server's init script creates.

This is test code, not engine code: it names dialects freely.
"""
from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DIALECTS = ("postgres", "mysql", "tsql")
DOMAINS = ("sales", "finance", "hr", "marketing", "ops")
SCHEMA_COUNT = 20
TABLES_PER_SCHEMA = 60
AUDIT_TABLES = ("events", "logins", "changes")
BIG_ROWS = 1_000_000
SMALL_ROWS = 1_000
BIG_TABLES = 3
SMALL_TABLES = 50
MIXED_CASE = ("CustomerAccounts", "OrderLines", "InvoiceHeaders", "PayrollRuns", "CampaignClicks",
              "StockLevels", "LedgerEntries", "ShipmentEvents", "TaxRates", "VendorContacts")
NOUNS = ("orders", "customers", "invoices", "payments", "accounts", "employees", "contracts", "products",
         "regions", "shipments", "budgets", "campaigns", "tickets", "assets", "vendors", "ledgers")
WORDS = ("amount", "status", "name", "code", "total", "created", "updated", "note", "score", "rate",
         "label", "owner", "level", "region", "kind", "flag", "ref", "count", "price", "weight")
PLAIN_KINDS = ("int", "bigint", "decimal", "text", "varchar", "date", "timestamp")
ODD_KINDS = ("bool", "uuid", "json", "binary", "xml", "array")
FILLED_KINDS = ("int", "bigint", "decimal", "text", "varchar")

TYPES: Dict[str, Dict[str, str]] = {
    "postgres": {"int": "integer", "bigint": "bigint", "decimal": "numeric(12,2)", "text": "text",
                 "varchar": "varchar(100)", "date": "date", "timestamp": "timestamp", "bool": "boolean",
                 "uuid": "uuid", "json": "jsonb", "binary": "bytea", "xml": "xml", "array": "integer[]"},
    # MySQL has no uuid, xml or array type: the nearest plain type stands in.
    "mysql": {"int": "int", "bigint": "bigint", "decimal": "decimal(12,2)", "text": "text",
              "varchar": "varchar(100)", "date": "date", "timestamp": "datetime", "bool": "boolean",
              "uuid": "char(36)", "json": "json", "binary": "varbinary(64)", "xml": "text", "array": "json"},
    # SQL Server 2022 has no json or array type.
    "tsql": {"int": "int", "bigint": "bigint", "decimal": "decimal(12,2)", "text": "nvarchar(max)",
             "varchar": "nvarchar(100)", "date": "date", "timestamp": "datetime2", "bool": "bit",
             "uuid": "uniqueidentifier", "json": "nvarchar(max)", "binary": "varbinary(64)", "xml": "xml",
             "array": "nvarchar(max)"},
}


@dataclass
class Column:
    name: str
    kind: str
    comment: Optional[str] = None
    references: Optional[str] = None  # a table in the same schema


@dataclass
class Table:
    name: str
    columns: List[Column]
    comment: Optional[str] = None
    rows: int = 0


@dataclass
class View:
    name: str
    table: str
    columns: List[str]  # empty: a row count


@dataclass
class Schema:
    name: str
    tables: List[Table] = field(default_factory=list)
    views: List[View] = field(default_factory=list)


def schema_names() -> List[str]:
    """The 20 numbered schemas, ``sales_01`` to ``ops_20``."""
    return [f"{DOMAINS[i % len(DOMAINS)]}_{i + 1:02d}" for i in range(SCHEMA_COUNT)]


def build(seed: int = 7) -> List[Schema]:
    """The whole wide schema, as data. Every random choice comes from ``seed``."""
    rng = random.Random(seed)
    names = schema_names()
    slots = [(s, t) for s in range(SCHEMA_COUNT) for t in range(TABLES_PER_SCHEMA)]
    chosen = rng.sample(slots, BIG_TABLES + SMALL_TABLES)
    rows = {slot: BIG_ROWS for slot in chosen[:BIG_TABLES]}
    rows.update({slot: SMALL_ROWS for slot in chosen[BIG_TABLES:]})
    # One mixed-case table in each of the first ten schemas, at a seeded position.
    mixed = {(s, rng.randrange(TABLES_PER_SCHEMA)): MIXED_CASE[s] for s in range(len(MIXED_CASE))}

    schemas = []
    for s, schema_name in enumerate(names):
        schema = Schema(schema_name)
        for t in range(TABLES_PER_SCHEMA):
            name = mixed.get((s, t)) or f"{NOUNS[rng.randrange(len(NOUNS))]}_{t + 1:03d}"
            count = rows.get((s, t), 0)
            # A big table stays narrow and has no foreign keys, so filling it is quick.
            width = 6 if count == BIG_ROWS else rng.randint(5, 30)
            parents = [] if count == BIG_ROWS else list(schema.tables)
            schema.tables.append(_table(rng, name, width, parents, count))
        schema.views = [
            View(f"v_{schema_name}_columns", schema.tables[0].name,
                 [c.name for c in schema.tables[0].columns[:4]]),
            View(f"v_{schema_name}_count", schema.tables[1].name, []),
        ]
        schemas.append(schema)

    audit = Schema("audit")
    for name in AUDIT_TABLES:
        audit.tables.append(_table(rng, name, rng.randint(5, 10), [], 0))
    audit.views = [View("v_audit_columns", audit.tables[0].name, [c.name for c in audit.tables[0].columns[:4]]),
                   View("v_audit_count", audit.tables[1].name, [])]
    schemas.append(audit)
    return schemas


def _comment(rng: random.Random, what: str) -> Optional[str]:
    return f"The {what}, generated for the test bed." if rng.random() < 0.3 else None


def _table(rng: random.Random, name: str, width: int, parents: List["Table"], rows: int) -> Table:
    columns = [Column("id", "bigint" if rows == BIG_ROWS else "int")]
    if parents and rng.random() < 0.5:
        parent = parents[rng.randrange(len(parents))]
        # The parent's id type: MySQL and SQL Server refuse an int key against a bigint id.
        columns.append(Column(f"{parent.name.lower()}_id", parent.columns[0].kind, references=parent.name))
    while len(columns) < width:
        kinds = ODD_KINDS if rng.random() < 0.15 else PLAIN_KINDS
        kind = kinds[rng.randrange(len(kinds))]
        word = WORDS[rng.randrange(len(WORDS))]
        columns.append(Column(f"{word}_{len(columns):02d}", kind))
    for column in columns:
        column.comment = _comment(rng, f"{column.name} column")
    return Table(name, columns, _comment(rng, f"{name} table"), rows)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def quote(dialect: str, name: str) -> str:
    if dialect == "mysql":
        return f"`{name}`"
    if dialect == "tsql":
        return f"[{name}]"
    return f'"{name}"'


def namespace(dialect: str, schema: str) -> str:
    """The schema's name on ``dialect``: MySQL's schemas are databases called ``wide_s01`` and so on."""
    if dialect != "mysql":
        return schema
    return "wide_audit" if schema == "audit" else f"wide_s{schema.rsplit('_', 1)[1]}"


def _literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def render(schemas: List[Schema], dialect: str) -> str:
    """The SQL file for ``dialect``. SQL Server's batches are separated with ``GO``."""
    if dialect not in DIALECTS:
        raise ValueError(f"unknown dialect {dialect!r}; expected one of {', '.join(DIALECTS)}")
    out: List[str] = [f"-- The nl2sql test bed's wide schema for {dialect}. Generated by testbed/wide_schema.py.\n"]
    go = "GO\n" if dialect == "tsql" else ""
    for schema in schemas:
        ns = quote(dialect, namespace(dialect, schema.name))
        out.append(f"CREATE DATABASE {ns};\n" if dialect == "mysql" else f"CREATE SCHEMA {ns};\n")
        out.append(go)
        for table in schema.tables:
            out.append(_create_table(dialect, ns, table))
            out.extend(_comments(dialect, ns, schema.name, table))
            if table.rows:
                out.append(_fill(dialect, ns, table))
            out.append(go)
        for view in schema.views:
            out.append(_create_view(dialect, ns, view))
            out.append(go)
    return "".join(out)


def _create_table(dialect: str, ns: str, table: Table) -> str:
    q = lambda name: quote(dialect, name)  # noqa: E731
    lines = []
    for column in table.columns:
        line = f"  {q(column.name)} {TYPES[dialect][column.kind]}"
        line += " NOT NULL PRIMARY KEY" if column.name == "id" else " NULL"
        if dialect == "mysql" and column.comment:
            line += f" COMMENT {_literal(column.comment)}"
        lines.append(line)
    for column in table.columns:
        if column.references:
            lines.append(f"  FOREIGN KEY ({q(column.name)}) REFERENCES {ns}.{q(column.references)} ({q('id')})")
    tail = f" COMMENT={_literal(table.comment)}" if dialect == "mysql" and table.comment else ""
    return f"CREATE TABLE {ns}.{q(table.name)} (\n" + ",\n".join(lines) + f"\n){tail};\n"


def _comments(dialect: str, ns: str, schema: str, table: Table) -> List[str]:
    if dialect == "mysql":
        return []  # inline, in CREATE TABLE
    q = lambda name: quote(dialect, name)  # noqa: E731
    out = []
    if dialect == "postgres":
        if table.comment:
            out.append(f"COMMENT ON TABLE {ns}.{q(table.name)} IS {_literal(table.comment)};\n")
        for column in table.columns:
            if column.comment:
                out.append(f"COMMENT ON COLUMN {ns}.{q(table.name)}.{q(column.name)} IS {_literal(column.comment)};\n")
        return out
    base = (f"EXEC sp_addextendedproperty @name = N'MS_Description', @value = N{{value}}, "
            f"@level0type = N'SCHEMA', @level0name = N{_literal(schema)}, "
            f"@level1type = N'TABLE', @level1name = N{_literal(table.name)}")
    if table.comment:
        out.append(base.format(value=_literal(table.comment)) + ";\n")
    for column in table.columns:
        if column.comment:
            out.append(base.format(value=_literal(column.comment))
                       + f", @level2type = N'COLUMN', @level2name = N{_literal(column.name)};\n")
    return out


def _fill(dialect: str, ns: str, table: Table) -> str:
    """One INSERT ... SELECT over a series made on the server."""
    q = lambda name: quote(dialect, name)  # noqa: E731
    expressions = {"int": "n % 97", "bigint": "n * 3", "decimal": "(n % 1000) / 10.0",
                   "text": "CONCAT('text ', n % 50)", "varchar": "CONCAT('v', n % 20)"}
    filled = [c for c in table.columns
              if c.name == "id" or (c.kind in FILLED_KINDS and not c.references)]
    names = ", ".join(q(c.name) for c in filled)
    values = ", ".join("n" if c.name == "id" else expressions[c.kind] for c in filled)
    if dialect == "postgres":
        series = f"generate_series(1, {table.rows}) AS g(n)"
    else:
        series = _digits_series(table.rows)
    return f"INSERT INTO {ns}.{q(table.name)} ({names}) SELECT {values} FROM {series};\n"


def _digits_series(rows: int) -> str:
    """1..rows from a cross join of digit tables (MySQL and SQL Server have no generate_series)."""
    places = len(str(rows - 1))
    digits = " UNION ALL ".join(f"SELECT {d} AS d" for d in range(10))
    joins = " CROSS JOIN ".join(f"({digits}) AS d{p}" for p in range(places))
    number = " + ".join(f"d{p}.d * {10 ** p}" for p in range(places))
    return f"(SELECT {number} + 1 AS n FROM {joins}) AS series WHERE n <= {rows}"


def _create_view(dialect: str, ns: str, view: View) -> str:
    q = lambda name: quote(dialect, name)  # noqa: E731
    select = ", ".join(q(c) for c in view.columns) if view.columns else "COUNT(*) AS row_count"
    return f"CREATE VIEW {ns}.{q(view.name)} AS SELECT {select} FROM {ns}.{q(view.table)};\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dialect", required=True, choices=DIALECTS)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, help="the file to write (default: standard output)")
    args = parser.parse_args(argv)
    sql = render(build(args.seed), args.dialect)
    if args.out:
        args.out.write_text(sql, encoding="utf-8", newline="\n")
    else:
        sys.stdout.write(sql)
    return 0


if __name__ == "__main__":
    sys.exit(main())
