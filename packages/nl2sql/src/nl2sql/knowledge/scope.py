"""The caller's view of the knowledge: by the names it lists and by the denied tables its text mentions (D39, D60).

An item is left out when it names a table the caller cannot read (a table,
a column of one, either side of a join, a term's or a rule's tables or
columns), or when any of its texts mentions, as a whole word and ignoring
case, the name of a table on that datasource the caller cannot read, or of a
table one of its roles denies by name (which covers a table kept out of the
index). The texts are every free text and every name an item carries: a rule
id and an enum code count. So knowledge never names a hidden table by its
exact name. A paraphrase ("the pay table"), a plural or singular ("salary"
for ``salaries``) and a name inside a longer identifier (``salaries_history``)
cannot be caught this way; the validator still refuses any plan on the table
(docs/architecture/wave_b.md, section 4).

The check needs the datasource's snapshot, to know which tables are denied:
without one, nothing is visible (fails closed).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from nl2sql_sdk.knowledge import DatasourceKnowledge
from nl2sql_sdk.schema import SchemaSnapshot

from nl2sql.knowledge.resolve import resolve_table

logger = logging.getLogger(__name__)

# Whether a caller may read ``(schema, table)``: a ``Scope``'s ``allows``.
Allows = Callable[[Optional[str], str], bool]


def denied_table_names(snapshot: SchemaSnapshot, allows: Allows) -> Set[str]:
    """The names (lower case) of the snapshot's tables the caller may not read."""
    return {t.table.table_name.lower() for t in snapshot.contract.tables.values()
            if not allows(t.table.schema_name, t.table.table_name)}


def deny_names(scope) -> Set[str]:
    """The table names (lower case) the caller's roles, or its policy check, deny by name on the scope's datasource.

    Only a pattern whose table part has no wildcard names a table, and only a
    name the scope still refuses counts (another role may allow it).
    """
    grants = [*scope.grants, *([scope.check] if scope.check is not None else [])]
    names = set()
    for grant in grants:
        for pattern in grant.deny:
            table = pattern.table
            if (table and not any(c in table for c in "*?[") and pattern.covers_datasource(scope.datasource_id)
                    and not scope.allows(pattern.schema if pattern.schema and pattern.schema != "*" else None, table)):
                names.add(table.lower())
    return names


def _pattern(names: Set[str]) -> Optional["re.Pattern[str]"]:
    """Any of ``names`` as a whole word, case ignored; None for no names."""
    if not names:
        return None
    return re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(n) for n in sorted(names)) + r")(?![A-Za-z0-9_])",
                      re.IGNORECASE)


def _mentions(texts: Iterable[Optional[str]], pattern: Optional["re.Pattern[str]"]) -> bool:
    return pattern is not None and any(text and pattern.search(text) for text in texts)


def _table_allowed(name: str, snapshot: SchemaSnapshot, allows: Allows) -> bool:
    """Whether the table ``name`` names may be read. A name that matches several tables needs them all."""
    table, why = resolve_table(name, snapshot)
    if table is not None:
        return allows(table.table.schema_name, table.table.table_name)
    schema, _, bare = name.strip().rpartition(".")
    if why and why.startswith("ambiguous"):
        return all(allows(t.table.schema_name, t.table.table_name) for t in snapshot.contract.tables.values()
                   if t.table.table_name.lower() == bare.lower())
    return allows(schema or None, bare)


def _column_allowed(name: str, snapshot: SchemaSnapshot, allows: Allows) -> bool:
    table, _, _ = name.strip().rpartition(".")
    return bool(table) and _table_allowed(table, snapshot, allows)


def _names(value) -> List[str]:
    return [value] if isinstance(value, str) else list(value)


def visible(knowledge: DatasourceKnowledge, snapshot: Optional[SchemaSnapshot], allows: Allows,
            also_denied: Iterable[str] = ()) -> DatasourceKnowledge:
    """What of ``knowledge`` a caller whose scope ``allows`` may see; nothing without a snapshot.

    ``also_denied``: more table names no text may mention (``deny_names(scope)``).
    """
    if snapshot is None:
        return DatasourceKnowledge()
    pattern = _pattern(denied_table_names(snapshot, allows) | {n.lower() for n in also_denied})

    def table_ok(name: str) -> bool:
        return _table_allowed(name, snapshot, allows)

    def column_ok(name: str) -> bool:
        return _column_allowed(name, snapshot, allows)

    tables = {name: item for name, item in knowledge.tables.items()
              if table_ok(name) and not _mentions([name, item.description, item.notes], pattern)}
    columns = {name: item for name, item in knowledge.columns.items()
               if column_ok(name) and not _mentions([name, item.description, item.notes, item.unit, *item.synonyms,
                                                     *item.values, *item.values.values()], pattern)}
    joins = [join for join in knowledge.joins
             if all(column_ok(n) for n in _names(join.from_) + _names(join.to))
             and not _mentions([join.notes], pattern)]
    terms = [term for term in knowledge.terms
             if all(table_ok(n) for n in term.tables) and all(column_ok(n) for n in term.columns)
             and not _mentions([term.term, term.definition, *term.synonyms], pattern)]
    rules = [rule for rule in knowledge.rules
             if all(table_ok(n) for n in rule.tables) and not _mentions([rule.id, rule.text], pattern)]
    examples = [example for example in knowledge.examples if not _mentions([example], pattern)]
    return DatasourceKnowledge.model_validate(
        {"tables": tables, "columns": columns, "joins": joins, "terms": terms, "rules": rules,
         "examples": examples}, context={"item_max_chars": 1 << 30})


def denied_pattern(snapshot: SchemaSnapshot, allows: Allows, also_denied: Iterable[str] = ()
                   ) -> Optional["re.Pattern[str]"]:
    """One regex for every table name the caller may not read (and ``also_denied``); None when there is none.

    Build it once and test many texts with ``mentions``: building it is a pass over the snapshot.
    """
    return _pattern(denied_table_names(snapshot, allows) | {n.lower() for n in also_denied})


def mentions(text: Optional[str], pattern: Optional["re.Pattern[str]"]) -> bool:
    """Whether ``text`` names, as a whole word and ignoring case, a table ``pattern`` matches."""
    return _mentions([text], pattern)


def mentions_denied(text: Optional[str], snapshot: SchemaSnapshot, allows: Allows,
                    also_denied: Iterable[str] = ()) -> bool:
    """Whether ``text`` mentions a table of ``snapshot`` the caller may not read (or one of ``also_denied``)."""
    return _mentions([text], _pattern(denied_table_names(snapshot, allows) | {n.lower() for n in also_denied}))


def caller_knowledge(ctx: Any, state: Mapping[str, Any]) -> Dict[str, DatasourceKnowledge]:
    """The knowledge a finished run's caller may see, per datasource the run planned on, for its explanation.

    Each datasource's knowledge is filtered with ``visible`` by the caller's
    scope (``state["user_context"]``) and the datasource's live snapshot, so a
    denied table's description, joins and terms never reach
    ``QueryResult.explanation``. A datasource the caller cannot reach, or
    without a snapshot, is left out. Never raises: on a failure it returns
    nothing and the explanation names every join a declared key.
    """
    from nl2sql.policies import UserContext

    try:
        # The runtime the run pinned (``run_with_graph`` hands it back), so the glossary and snapshot are the
        # ones the prompts used, not what a reload swapped in meanwhile.
        ctx = state.get("pinned_runtime") or ctx
        knowledge = getattr(ctx, "knowledge", None)
        if knowledge is None or not knowledge.datasources:
            return {}
        user = state.get("user_context") or UserContext()
        user = user if isinstance(user, UserContext) else UserContext.model_validate(user)
        ids = set()
        for output in (state.get("subgraph_outputs") or {}).values():
            sub_query = output.get("sub_query") if isinstance(output, dict) else getattr(output, "sub_query", None)
            ds = sub_query.get("datasource_id") if isinstance(sub_query, dict) else getattr(sub_query, "datasource_id",
                                                                                              None)
            if ds in knowledge.datasources:
                ids.add(ds)
        shown: Dict[str, DatasourceKnowledge] = {}
        for ds in sorted(ids):
            scope = ctx.policy_engine.scope(user, ds)
            snapshot = ctx.vector_store.snapshot(ds) if scope.reaches_datasource() else None
            if snapshot is not None:
                shown[ds] = visible(knowledge.get(ds), snapshot, scope.allows, deny_names(scope))
        return shown
    except Exception:  # the explanation is still worth having without the glossary
        logger.exception("Could not filter the knowledge for the explanation")
        return {}


def _prefixes(knowledge: DatasourceKnowledge) -> List[str]:
    """The provenance key of each item in ``knowledge`` (``tables.orders``, ``terms.revenue``, ...)."""
    def side(value) -> str:
        return ",".join(_names(value))

    return ([f"tables.{n}" for n in knowledge.tables] + [f"columns.{n}" for n in knowledge.columns]
            + [f"joins.{side(j.from_)}->{side(j.to)}" for j in knowledge.joins]
            + [f"terms.{t.term}" for t in knowledge.terms] + [f"rules.{r.id}" for r in knowledge.rules]
            + [f"examples.{e}" for e in knowledge.examples])


def with_schema_facts(shown: DatasourceKnowledge, provenance: Dict[str, str], snapshot: SchemaSnapshot,
                      allows: Allows, *, dialect: Optional[str], also_denied: Iterable[str] = ()
                      ) -> Tuple[DatasourceKnowledge, Dict[str, str], Dict[str, str]]:
    """``shown`` (already filtered) with what the schema adds, as ``show`` returns it, and each item's origin.

    The database's own table and column comments fill a description no
    knowledge source set (origin ``database``), and the roles the snapshot
    proves fill a role none set (origin ``derived``), on tables the caller
    may read and only where the text mentions no denied table. Returns the
    knowledge, the origins and the knowledge source of each item.
    """
    from nl2sql.knowledge.resolve import derived_roles, resolve_column, resolve_table, table_label

    pattern = _pattern(denied_table_names(snapshot, allows) | {n.lower() for n in also_denied})
    prefixes = _prefixes(shown)
    origins: Dict[str, str] = {}
    sources: Dict[str, str] = {}
    for key, label in provenance.items():
        if any(key == p or key.startswith(p + ".") for p in prefixes):
            origins[key], sources[key] = "knowledge", label

    data = shown.model_dump(mode="json", by_alias=True, exclude_unset=True)
    tables: Dict[str, Dict] = data.setdefault("tables", {})
    columns: Dict[str, Dict] = data.setdefault("columns", {})
    table_keys, column_keys = {}, {}
    for name in tables:
        table, _ = resolve_table(name, snapshot)
        if table is not None:
            table_keys[table_label(table, snapshot).lower()] = name
    for name in columns:
        table, column, _ = resolve_column(name, snapshot)
        if column is not None:
            column_keys[f"{table_label(table, snapshot)}.{column.name}".lower()] = name

    def fill(items: Dict[str, Dict], key: str, field: str, value: str, origin: str, kind: str) -> None:
        item = items.setdefault(key, {})
        if item.get(field) is None:
            item[field] = value
            origins[f"{kind}.{key}.{field}"] = origin

    for contract_key, contract in snapshot.contract.tables.items():
        if not allows(contract.table.schema_name, contract.table.table_name):
            continue
        label = table_label(contract, snapshot)
        metadata = snapshot.metadata.tables.get(contract_key)
        if metadata is None:
            continue
        if metadata.description and not _mentions([metadata.description], pattern):
            fill(tables, table_keys.get(label.lower(), label), "description", metadata.description, "database",
                 "tables")
        for column_name, column in metadata.columns.items():
            name = f"{label}.{column_name}"
            if column.description and not _mentions([column.description], pattern):
                fill(columns, column_keys.get(name.lower(), name), "description", column.description, "database",
                     "columns")
    for name, role in derived_roles(snapshot, dialect=dialect).items():
        table, _, _ = resolve_column(name, snapshot)
        if table is not None and allows(table.table.schema_name, table.table.table_name):
            fill(columns, column_keys.get(name.lower(), name), "role", role, "derived", "columns")
    full = DatasourceKnowledge.model_validate(data, context={"item_max_chars": 1 << 30})
    return full, origins, sources
