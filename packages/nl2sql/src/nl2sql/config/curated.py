"""The body of the ``curated`` section: verified queries and metrics, per datasource, and their checks.

A verified query is SQL a person wrote, in the dialect of its datasource, with
typed ``:name`` parameters and the questions it answers. The models check what
needs no database: shape, unique ids, question placeholders against declared
params, and values against declared types. :func:`check_curated` adds what
needs the datasources: the datasource exists, and the SQL, parsed in that
datasource's dialect, is one read-only query whose placeholders are exactly
the declared params.

The dialect always comes from the datasource's adapter (``get_dialect()``);
nothing here names one. sqlglot is imported inside the checks, so loading a
config does not pay for it.
"""
from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Dict, List, Literal, Mapping, Optional, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

if TYPE_CHECKING:
    from nl2sql.config.models import Curated

ParamValue = Union[StrictInt, StrictFloat, StrictStr]

_SLUG = r"^[a-z][a-z0-9_]*$"
_QUESTION_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _typed(kind: str, value: ParamValue) -> Union[int, float, str, date]:
    """``value`` as the declared ``kind``, or ``ValueError``."""
    if kind == "int":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise ValueError(f"{value!r} is not an int")
    if kind == "float":
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        raise ValueError(f"{value!r} is not a number")
    if kind == "date":
        if isinstance(value, str):
            try:
                return date.fromisoformat(value)
            except ValueError:
                pass
        raise ValueError(f"{value!r} is not a date (YYYY-MM-DD)")
    if isinstance(value, str):
        return value
    raise ValueError(f"{value!r} is not a string")


class ParamSpec(BaseModel):
    """One typed parameter of a verified query."""

    type: Literal["int", "float", "string", "date"]
    default: Optional[ParamValue] = None
    min: Optional[ParamValue] = None
    max: Optional[ParamValue] = None
    choices: Optional[List[StrictStr]] = None
    example: Optional[ParamValue] = None

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def _values_agree_with_the_type(self) -> "ParamSpec":
        if self.type == "string" and (self.min is not None or self.max is not None):
            raise ValueError("min/max apply to int, float and date params, not string")
        if self.choices is not None and self.type != "string":
            raise ValueError("choices apply to string params only")

        bounds = {}
        for name in ("min", "max"):
            value = getattr(self, name)
            if value is not None:
                try:
                    bounds[name] = _typed(self.type, value)
                except ValueError as exc:
                    raise ValueError(f"{name}: {exc}") from None
        if "min" in bounds and "max" in bounds and bounds["min"] > bounds["max"]:
            raise ValueError(f"min {self.min!r} is greater than max {self.max!r}")

        for name in ("default", "example"):
            value = getattr(self, name)
            if value is None:
                continue
            try:
                typed = _typed(self.type, value)
            except ValueError as exc:
                raise ValueError(f"{name}: {exc}") from None
            if "min" in bounds and typed < bounds["min"]:
                raise ValueError(f"{name} {value!r} is below min {self.min!r}")
            if "max" in bounds and typed > bounds["max"]:
                raise ValueError(f"{name} {value!r} is above max {self.max!r}")
            if self.choices is not None and value not in self.choices:
                raise ValueError(f"{name} {value!r} is not one of choices {self.choices}")
        return self


class VerifiedQuery(BaseModel):
    """A query a person wrote and reviewed, and the questions it answers."""

    id: str = Field(pattern=_SLUG)
    description: str
    questions: List[str] = Field(min_length=1)
    params: Dict[str, ParamSpec] = Field(default_factory=dict)
    sql: str

    model_config = ConfigDict(extra="forbid", frozen=True)

    @model_validator(mode="after")
    def _placeholders_match_params(self) -> "VerifiedQuery":
        used = {name for q in self.questions for name in _QUESTION_PLACEHOLDER.findall(q)}
        undeclared = sorted(used - set(self.params))
        if undeclared:
            raise ValueError(f"query '{self.id}': question placeholders {undeclared} are not declared params")
        unused = sorted(set(self.params) - used)
        if unused:
            raise ValueError(f"query '{self.id}': params {unused} appear in no question")
        return self


class MetricDefinition(BaseModel):
    """A named business expression. Parsed so the format is fixed; not used yet."""

    name: str
    description: str = ""
    expression: str
    table: str

    model_config = ConfigDict(extra="forbid", frozen=True)


class CuratedDatasource(BaseModel):
    """The curated answers of one datasource."""

    verified_queries: List[VerifiedQuery] = Field(default_factory=list)
    metrics: List[MetricDefinition] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("verified_queries")
    @classmethod
    def _unique_ids(cls, queries: List[VerifiedQuery]) -> List[VerifiedQuery]:
        ids = [q.id for q in queries]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate verified query ids: {duplicates}")
        return queries


# --------------------------------------------------------------------------
# The SQL checks, against the configured datasources
# --------------------------------------------------------------------------

class CuratedSqlError(ValueError):
    """A verified query's SQL is not one read-only query."""


@functools.lru_cache(maxsize=None)
def _write_nodes() -> Tuple[type, ...]:
    from sqlglot import exp

    # Alter was AlterTable in older sqlglot; TruncateTable and Into are newer.
    names = ("Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable",
             "Command", "TruncateTable", "Into")
    return tuple(getattr(exp, n) for n in names if hasattr(exp, n))


@dataclass(frozen=True)
class VerifiedSql:
    """What the checks read from a verified query's SQL."""

    placeholders: Set[str]
    tables: List[Tuple[str, str]]  # (schema or "", table), CTE names excluded
    # Every column it references, lower-cased: (schema or "", table, column). The table is the one
    # its qualifier (or the only table read) names, or "" when it cannot be told.
    columns: Set[Tuple[str, str, str]] = field(default_factory=set)


def inspect_verified_sql(sql: str, dialect: str) -> VerifiedSql:
    """Parse ``sql`` in ``dialect`` and check it is exactly one read-only query.

    Returns its ``:name`` placeholders, the tables it reads and the column
    names it references (schema drift's impact reads them). Raises
    :class:`CuratedSqlError` otherwise.
    """
    import sqlglot
    from sqlglot import exp

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
        if isinstance(node, _write_nodes()):
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
    aliases = {t.alias_or_name.lower(): ((t.db or "").lower(), t.name.lower())
               for t in tree.find_all(exp.Table) if t.name and not (not t.db and t.name.lower() in ctes)}
    only = next(iter(aliases.values())) if len(set(aliases.values())) == 1 else ("", "")
    columns = {(*(aliases.get(c.table.lower(), ("", "")) if c.table else only), c.name.lower())
               for c in tree.find_all(exp.Column) if c.name}
    return VerifiedSql(placeholders=placeholders, tables=tables, columns=columns)


@dataclass
class CuratedIssue:
    """One problem in the ``curated`` section. ``query_id`` is None for a datasource-level one."""

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


def check_curated_queries(cfg: "Curated", dialects: Mapping[str, str]) -> CuratedCheck:
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


def check_curated(cfg: "Curated", dialects: Mapping[str, str]) -> List[CuratedIssue]:
    """The issues :func:`check_curated_queries` finds; empty when the file is good."""
    return check_curated_queries(cfg, dialects).issues
