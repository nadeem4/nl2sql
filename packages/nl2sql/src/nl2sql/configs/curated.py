"""``curated.yaml``: verified queries a data team writes, and the checks on them.

A verified query is SQL a person wrote, in the dialect of its datasource, with
typed ``:name`` parameters and the questions it answers. The models, in
:mod:`nl2sql.config.curated`, check what needs no database: shape, unique ids,
question placeholders against declared params, and values against declared
types. :func:`check_curated` adds what
needs the datasources: the datasource exists, and the SQL, parsed in that
datasource's dialect, is one read-only query whose placeholders are exactly
the declared params.

The dialect always comes from the datasource's adapter (``get_dialect()``);
nothing here names one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Set, Tuple

import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

from nl2sql.config.curated import (  # noqa: F401 -- the models live with the config
    CuratedDatasource,
    MetricDefinition,
    ParamSpec,
    ParamValue,
    VerifiedQuery,
)


class CuratedFileConfig(BaseModel):
    """File-level schema for ``curated.yaml``, keyed by datasource id."""

    version: int = 1
    datasources: Dict[str, CuratedDatasource] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------
# The SQL checks
# --------------------------------------------------------------------------

class CuratedSqlError(ValueError):
    """A verified query's SQL is not one read-only query."""


def _write_nodes() -> Tuple[type, ...]:
    # Alter was AlterTable in older sqlglot; TruncateTable and Into are newer.
    names = ("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable",
             "Command", "TruncateTable", "Into")
    return tuple(getattr(exp, n) for n in names if hasattr(exp, n))


WRITE_NODES = _write_nodes()


@dataclass(frozen=True)
class VerifiedSql:
    """What the checks read from a verified query's SQL."""

    placeholders: Set[str]
    tables: List[Tuple[str, str]]  # (schema or "", table), CTE names excluded


def inspect_verified_sql(sql: str, dialect: str) -> VerifiedSql:
    """Parse ``sql`` in ``dialect`` and check it is exactly one read-only query.

    Returns its ``:name`` placeholders and the tables it reads. Raises
    :class:`CuratedSqlError` otherwise.
    """
    try:
        sqlglot.Dialect.get_or_raise(dialect)
    except Exception as exc:  # noqa: BLE001 -- sqlglot raises ValueError or its own
        raise CuratedSqlError(f"unknown SQL dialect {dialect!r}: {exc}") from None
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except Exception as exc:  # noqa: BLE001 -- ParseError, TokenError
        raise CuratedSqlError(f"the SQL does not parse: {exc}") from None
    if len(statements) != 1:
        raise CuratedSqlError(f"the SQL must be exactly one statement, found {len(statements)}")

    [tree] = statements
    if not isinstance(tree, exp.Query):
        raise CuratedSqlError(f"the SQL must be a read-only query, found {type(tree).__name__.upper()}")
    for node in tree.walk():
        node = node[0] if isinstance(node, tuple) else node  # older sqlglot yields tuples
        if isinstance(node, WRITE_NODES):
            raise CuratedSqlError(f"the SQL must be read-only, found {type(node).__name__.upper()}")

    placeholders: Set[str] = set()
    for node in tree.find_all(exp.Placeholder):
        name = node.this if isinstance(node.this, str) else None
        if not name or name == "?":
            raise CuratedSqlError("positional placeholders ('?') are not supported; use :name")
        placeholders.add(name)

    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables = sorted({(t.db or "", t.name) for t in tree.find_all(exp.Table)
                     if t.name and not (not t.db and t.name.lower() in ctes)})
    return VerifiedSql(placeholders=placeholders, tables=tables)


@dataclass
class CuratedIssue:
    """One problem in ``curated.yaml``. ``query_id`` is None for a datasource-level one."""

    datasource_id: str
    query_id: Optional[str]
    message: str

    def __str__(self) -> str:
        where = f"{self.datasource_id}.{self.query_id}" if self.query_id else self.datasource_id
        return f"{where}: {self.message}"


@dataclass
class CuratedCheck:
    """The result of :func:`check_curated_queries`: issues, and each query's parsed SQL."""

    issues: List[CuratedIssue] = field(default_factory=list)
    parsed: Dict[Tuple[str, str], VerifiedSql] = field(default_factory=dict)


def check_curated_queries(cfg: CuratedFileConfig, dialects: Mapping[str, str]) -> CuratedCheck:
    """Check ``cfg`` against the configured datasources.

    ``dialects`` maps each configured datasource id to its adapter's
    ``get_dialect()``. Needs no database connection and no index.
    """
    check = CuratedCheck()
    for ds_id, ds in cfg.datasources.items():
        if ds_id not in dialects:
            check.issues.append(CuratedIssue(ds_id, None, "not a configured datasource"))
            continue
        for query in ds.verified_queries:
            try:
                parsed = inspect_verified_sql(query.sql, dialects[ds_id])
            except CuratedSqlError as exc:
                check.issues.append(CuratedIssue(ds_id, query.id, str(exc)))
                continue
            declared = set(query.params)
            problems = []
            if parsed.placeholders - declared:
                problems.append(f"SQL placeholders {sorted(parsed.placeholders - declared)} are not declared params")
            if declared - parsed.placeholders:
                problems.append(f"params {sorted(declared - parsed.placeholders)} are not used in the SQL")
            if problems:
                check.issues.append(CuratedIssue(ds_id, query.id, "; ".join(problems)))
                continue
            check.parsed[(ds_id, query.id)] = parsed
    return check


def check_curated(cfg: CuratedFileConfig, dialects: Mapping[str, str]) -> List[CuratedIssue]:
    """The issues :func:`check_curated_queries` finds; empty when the file is good."""
    return check_curated_queries(cfg, dialects).issues
