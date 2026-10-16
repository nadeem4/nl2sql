"""What a schema diff breaks, from data the engine already has (docs/architecture/wave_a.md, section 10).

``drift_impact(ctx, diff, tables)`` runs every finder in ``FINDERS`` over the
diff; each fills its own field of ``DriftImpact``. Something new that refers
to tables (Wave B's glossary items, ``_glossary_items``) is one more field
and one more finder, and no caller changes. The diff given is the one the
caller may see, so the impact names only tables the caller may read.

The plan cache needs no entry: it is keyed on ``schema_version``, so a
rebuild already makes its plans unreachable.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, List, Literal, Tuple

from pydantic import BaseModel, Field

from nl2sql_sdk.schema import SchemaDiff, TableRef


class ImpactedQuery(BaseModel):
    """A verified query whose SQL reads something the diff dropped or retyped; ``reasons`` say what."""

    datasource_id: str
    query_id: str
    reasons: List[str]


class ImpactedRule(BaseModel):
    """A role's ``allow`` or ``deny`` pattern that matched a dropped table and now matches nothing."""

    role: str
    kind: Literal["allow", "deny"]
    pattern: str


class DriftImpact(BaseModel):
    verified_queries: List[ImpactedQuery] = Field(default_factory=list)
    policy_rules: List[ImpactedRule] = Field(default_factory=list)
    preferred_tables: List[str] = Field(default_factory=list)  # the datasource's entries that were dropped
    # Glossary items (``terms.revenue``, ``columns.orders.note``) that named a dropped table or column (Wave B, K1).
    glossary_items: List[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.verified_queries or self.policy_rules or self.preferred_tables or self.glossary_items)


# A finder: (ctx, diff, the datasource's tables now) -> fills its field of the impact.
Finder = Callable[[Any, SchemaDiff, List[TableRef], DriftImpact], None]


def drift_impact(ctx: Any, diff: SchemaDiff, tables: Iterable[TableRef]) -> DriftImpact:
    """What ``diff`` breaks. ``tables`` are the datasource's tables on the diff's new side."""
    impact = DriftImpact()
    tables = list(tables)
    for finder in FINDERS:
        finder(ctx, diff, tables, impact)
    return impact


def _same_table(ref: TableRef, schema: str, name: str) -> bool:
    """Whether a query's ``schema.name`` (schema may be empty) names ``ref``; case is ignored."""
    return ref.table_name.lower() == name.lower() and (not schema or ref.schema_name.lower() == schema.lower())


def _verified_queries(ctx: Any, diff: SchemaDiff, tables: List[TableRef], impact: DriftImpact) -> None:
    """Each verified query reading a dropped table or column, or a column whose type changed.

    From the same sqlglot parse ``nl2sql curated validate`` uses. A column is
    matched by its table: a qualified column (``o.note``) by the table its
    alias names, an unqualified one in a single-table query by that table.
    An unqualified column in a query of several tables cannot be told apart,
    so it is reported as one the query *may* read.
    """
    curated = getattr(ctx, "curated", None)
    section = curated.datasources.get(diff.datasource_id) if curated is not None else None
    if section is None or not section.verified_queries:
        return
    from nl2sql.config.curated import CuratedSqlError, inspect_verified_sql

    try:
        dialect = ctx.ds_registry.get_dialect(diff.datasource_id)
    except ValueError:  # unavailable: its queries cannot be parsed in its dialect
        return
    columns: List[Tuple[TableRef, str, str]] = (
        [(c.table, c.column_name, "dropped") for c in diff.columns_dropped]
        + [(c.table, c.column, "retyped") for c in diff.columns_changed if "type" in c.changes])
    for query in section.verified_queries:
        try:
            parsed = inspect_verified_sql(query.sql, dialect)
        except CuratedSqlError:
            continue  # startup and `nl2sql curated validate` report it
        reasons = [f"reads dropped table {ref.schema_name}.{ref.table_name}"
                   for ref in diff.tables_dropped if any(_same_table(ref, s, n) for s, n in parsed.tables)]
        for ref, column, what in columns:
            name = column.lower()
            if any(c == name and t and _same_table(ref, s, t) for s, t, c in parsed.columns):
                reasons.append(f"reads {what} column {ref.schema_name}.{ref.table_name}.{column}")
            elif (any(c == name and not t for _, t, c in parsed.columns)
                  and any(_same_table(ref, s, n) for s, n in parsed.tables)):
                reasons.append(f"may read {what} column {ref.schema_name}.{ref.table_name}.{column}")
        if reasons:
            impact.verified_queries.append(ImpactedQuery(datasource_id=diff.datasource_id, query_id=query.id,
                                                         reasons=reasons))


def _policy_rules(ctx: Any, diff: SchemaDiff, tables: List[TableRef], impact: DriftImpact) -> None:
    if not diff.tables_dropped:
        return
    from nl2sql.policies import patterns_matching

    roles = ctx.config.policies.roles
    dropped = patterns_matching(roles, diff.datasource_id,
                                [(t.schema_name, t.table_name) for t in diff.tables_dropped])
    still = patterns_matching(roles, diff.datasource_id, [(t.schema_name, t.table_name) for t in tables])
    impact.policy_rules = [ImpactedRule(role=role, kind=kind, pattern=pattern)
                           for role, kind, pattern in sorted(dropped - still)]


def _preferred_tables(ctx: Any, diff: SchemaDiff, tables: List[TableRef], impact: DriftImpact) -> None:
    from nl2sql.datasources.models import matches_table

    preferred = ctx.ds_registry.get_preferred_tables(diff.datasource_id)
    impact.preferred_tables = [name for name in preferred
                               if any(matches_table(name, ref) for ref in diff.tables_dropped)]


def _glossary_items(ctx: Any, diff: SchemaDiff, tables: List[TableRef], impact: DriftImpact) -> None:
    """Each glossary item that named a dropped table, or a column that was dropped (its table's or its own).

    Names are matched as the glossary writes them: ``table``, ``schema.table``,
    ``table.column`` or ``schema.table.column``, case ignored.
    """
    knowledge = getattr(ctx, "knowledge", None)
    block = knowledge.datasources.get(diff.datasource_id) if knowledge is not None else None
    if block is None or not (diff.tables_dropped or diff.columns_dropped):
        return
    # The caller's view of the glossary (D60): only items on tables it may read (``tables`` and the dropped
    # tables it sees) whose text mentions no other table of the stored snapshot. No snapshot: none (fails closed).
    from nl2sql.knowledge.scope import visible

    readable = [*tables, *diff.tables_dropped]
    block = visible(block, ctx.vector_store.snapshot(diff.datasource_id),
                    lambda schema, name: any(_same_table(ref, schema or "", name) for ref in readable))

    def table_dropped(name: str) -> bool:
        schema, _, table = name.rpartition(".")
        return any(_same_table(ref, schema, table) for ref in diff.tables_dropped)

    def column_dropped(name: str) -> bool:
        table, _, column = name.rpartition(".")
        if not table:
            return False
        schema, _, bare = table.rpartition(".")
        return table_dropped(table) or any(
            c.column_name.lower() == column.lower() and _same_table(c.table, schema, bare)
            for c in diff.columns_dropped)

    def sides(value) -> List[str]:
        return [value] if isinstance(value, str) else list(value)

    found = [f"tables.{name}" for name in block.tables if table_dropped(name)]
    found += [f"columns.{name}" for name in block.columns if column_dropped(name)]
    found += [f"joins.{','.join(sides(j.from_))}->{','.join(sides(j.to))}" for j in block.joins
              if any(column_dropped(n) for n in sides(j.from_) + sides(j.to))]
    found += [f"terms.{t.term}" for t in block.terms
              if any(table_dropped(n) for n in t.tables) or any(column_dropped(n) for n in t.columns)]
    found += [f"rules.{r.id}" for r in block.rules if any(table_dropped(n) for n in r.tables)]
    impact.glossary_items = found


FINDERS: List[Finder] = [_verified_queries, _policy_rules, _preferred_tables, _glossary_items]
