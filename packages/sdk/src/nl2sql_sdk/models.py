"""What an adapter is built from and reports: its connection, limits and index scope, and a connection check."""
from __future__ import annotations

from fnmatch import fnmatchcase
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, PositiveInt, field_validator

from .schema import TableRef


class ConnectionModel(BaseModel):
    """The base of every adapter's connection settings: the ``connection`` block of a datasource.

    Strict: an unknown key is an error, so a typo (``hostname`` for ``host``)
    fails when the engine builds the adapter instead of being ignored. A
    validation error never echoes its input. Declare a credential as
    ``SecretStr``: it never prints, and the generated form shows it as a
    password. ``Field(title, description, examples)`` label the form, and
    ``json_schema_extra={"group": "advanced"}`` folds a field away.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Limits(BaseModel):
    """A datasource's limits, given to its adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    row_limit: Optional[PositiveInt] = None   # the most rows one query returns
    timeout_ms: Optional[PositiveInt] = None  # the longest one statement may run
    max_bytes: Optional[PositiveInt] = None


def _matches(name: str, patterns: List[str]) -> bool:
    name = name.lower()
    return any(fnmatchcase(name, pattern.lower()) for pattern in patterns)


class IndexScope(BaseModel):
    """What of a datasource gets indexed: a datasource's ``index:`` block.

    Every pattern is a glob that ignores case. Schemas are matched by name,
    tables as ``schema.table`` and columns as ``schema.table.column``. An
    empty ``include_*`` list means everything; an exclude wins over an
    include. The adapter leaves its own system schemas out whatever the scope
    says. ``exclude_columns`` keeps a column out of the index; it is not
    access control.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    include_schemas: List[str] = []   # empty: every schema but the adapter's system schemas
    exclude_schemas: List[str] = []
    include_tables: List[str] = []    # "schema.table" globs
    exclude_tables: List[str] = []
    exclude_columns: List[str] = []   # "schema.table.column" globs
    profile: Literal["catalog", "stats"] = "stats"
    sample_values: bool = False
    max_profile_rows: PositiveInt = 1_000_000

    @field_validator("include_tables", "exclude_tables")
    @classmethod
    def _tables_name_a_schema(cls, patterns: List[str]) -> List[str]:
        for pattern in patterns:
            if "." not in pattern:
                raise ValueError(f"'{pattern}' needs a schema: write '*.{pattern}'")
        return patterns

    @field_validator("exclude_columns")
    @classmethod
    def _columns_name_a_table(cls, patterns: List[str]) -> List[str]:
        for pattern in patterns:
            if pattern.count(".") < 2:
                raise ValueError(f"'{pattern}' needs a schema and a table: write 'schema.table.column'")
        return patterns

    def wants_table(self, ref: TableRef) -> bool:
        """Whether the table (or view) ``ref`` is indexed."""
        schema, table = ref.schema_name, f"{ref.schema_name}.{ref.table_name}"
        if self.include_schemas and not _matches(schema, self.include_schemas):
            return False
        if _matches(schema, self.exclude_schemas):
            return False
        if self.include_tables and not _matches(table, self.include_tables):
            return False
        return not _matches(table, self.exclude_tables)

    def wants_column(self, ref: TableRef, column: str) -> bool:
        """Whether ``column`` of the table ``ref`` is indexed and profiled."""
        return not _matches(f"{ref.schema_name}.{ref.table_name}.{column}", self.exclude_columns)


class ConnectionCheck(BaseModel):
    """What ``test_connection`` found: ``ok``, or the stage that failed, why, and the fix."""

    ok: bool
    stage: Optional[Literal["config", "driver", "network", "auth", "database", "permission"]] = None
    message: Optional[str] = None
    fix: Optional[str] = None
    server_version: Optional[str] = None
