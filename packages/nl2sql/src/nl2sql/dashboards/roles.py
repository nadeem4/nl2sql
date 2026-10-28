"""The columns of a datasource as the starter dashboard reads them (docs/architecture/wave_c.md, section 6, D115).

Three layers, no query and no model: Wave B's derived roles (a key is an ``identifier``, a temporal type a
``date``), then its proposed heuristics (``find_roles``), then what the glossary says, which wins. The dashboard then
reads each role as a **measure** (``money``, ``quantity``), a **time** (``date``), a **dimension** (``enum``, or a
text column with 2 to ``max_categories`` distinct values) or an **identifier** (never charted). Only tables the
caller's scope reaches are read. A heuristic role nobody accepted only picks a tile; it is never sent to a model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from nl2sql_sdk.knowledge import ColumnKnowledge, DatasourceKnowledge
from nl2sql_sdk.schema import ColumnContract, SchemaSnapshot, TableContract

from nl2sql.knowledge.resolve import derived_roles, resolve_column, table_label
from nl2sql.profiler.roles import find_roles

Allows = Callable[[Optional[str], str], bool]


@dataclass(frozen=True)
class Source:
    """What ``generate`` reads: the live snapshot, the merged glossary, the caller's scope and the limits."""

    datasource_id: str
    snapshot: SchemaSnapshot
    dialect: Optional[str]
    allows: Allows
    glossary: DatasourceKnowledge
    enum_max_distinct: int  # settings.profiler.enum_max_distinct
    max_categories: int  # settings.charts.max_categories


@dataclass(frozen=True)
class Col:
    contract: ColumnContract
    distinct: int  # 0 when the snapshot has no statistics
    null_percentage: float
    maximum: Any = None  # the snapshot's largest value (a date column's latest day)
    kind: str = "other"  # number, text, temporal, boolean or other: the column's type category

    @property
    def name(self) -> str:
        return self.contract.name


@dataclass(frozen=True)
class Table:
    contract: TableContract
    rows: int
    measures: List[Col] = field(default_factory=list)
    times: List[Col] = field(default_factory=list)
    dimensions: List[Col] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.contract.table.table_name


def column_roles(source: Source) -> List[Table]:
    """The tables the caller reaches, ranked: more rows first, then more measures, then by name. In each table the
    measures come by non-null share, then name; the dimensions by distinct count (fewer first), then name."""
    snapshot = source.snapshot
    derived = derived_roles(snapshot, dialect=source.dialect)
    said = _said(source.glossary, snapshot)
    proposed = {(f.table.table.full_name, f.column.name): f.change.get("role") for f in find_roles(
        snapshot, dialect=source.dialect, allows=source.allows, said=said, enum_max_distinct=source.enum_max_distinct)}
    tables: List[Table] = []
    for key, contract in snapshot.contract.tables.items():
        ref = contract.table
        if not source.allows(ref.schema_name, ref.table_name):
            continue
        metadata = snapshot.metadata.tables.get(key)
        measures: List[Col] = []
        times: List[Col] = []
        dimensions: List[Col] = []
        for column in contract.columns.values():
            statistics = metadata.columns[column.name].statistics if metadata and column.name in metadata.columns else None
            kind = _category(column.data_type, source.dialect)
            col = Col(column, statistics.distinct_count if statistics else 0,
                      statistics.null_percentage if statistics else 0.0, statistics.max_value if statistics else None,
                      kind)
            glossary = said.get((contract.full_name, column.name.lower()))
            role = (glossary.role if glossary else None) or derived.get(
                f"{table_label(contract, snapshot)}.{column.name}") or proposed.get((contract.full_name, column.name))
            if role in ("money", "quantity") and kind == "number":
                measures.append(col)
            elif role == "date" and kind in ("temporal", "text"):
                times.append(col)
            elif role == "enum" or (role in (None, "text") and kind == "text"
                                    and 2 <= col.distinct <= source.max_categories):
                dimensions.append(col)
        rows = (metadata.row_count if metadata else None) or 0
        tables.append(Table(contract, rows,
                            sorted(measures, key=lambda c: (c.null_percentage, c.name)),
                            sorted(times, key=lambda c: (c.null_percentage, c.name)),
                            sorted(dimensions, key=lambda c: (c.distinct, c.name))))
    return sorted(tables, key=lambda t: (-t.rows, -len(t.measures), t.name))


def _category(data_type: str, dialect: Optional[str]) -> str:
    from nl2sql.pipeline.nodes.validator.join_keys import column_category

    return column_category(data_type, dialect)


def _said(glossary: DatasourceKnowledge, snapshot: SchemaSnapshot) -> Dict[Tuple[str, str], ColumnKnowledge]:
    """What the glossary says about each column it names, keyed ``(table's full name, lower-case column)``."""
    said: Dict[Tuple[str, str], ColumnKnowledge] = {}
    for name, knowledge in glossary.columns.items():
        table, column, _ = resolve_column(name, snapshot)
        if table is not None and column is not None:
            said[(table.full_name, column.name.lower())] = knowledge
    return said
