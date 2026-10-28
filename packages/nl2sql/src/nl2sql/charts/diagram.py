"""Mermaid diagrams of an answer and of a schema (docs/architecture/wave_c.md, section 4, "Diagrams"; D113).

``diagram_for`` draws the tables an answer read from its plans and nothing else: no row, no model call. ``schema_diagram``
draws a schema view that ``datasources.schema`` has already cut to the caller's scope. Both write Mermaid with no colour,
no ``%%{init}`` line and no ``style``, ``classDef``, ``linkStyle`` or ``click``: the reader picks the theme.

Every name that reaches the text is a database's or a model's, so none is trusted. A label keeps letters, digits and a few
marks and is always quoted; an identifier is ASCII letters, digits and ``_`` and never starts with a word Mermaid reads; and neither can hold a newline, so a name
cannot start a line of its own.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

if TYPE_CHECKING:
    from nl2sql.api.query_api import QueryResult

MAX_TABLES = 30
_LABEL_MAX = 60
# Words Mermaid reads at the start of a line.
_RESERVED = {"style", "classdef", "class", "click", "linkstyle", "direction", "title", "subgraph", "end", "erdiagram",
             "flowchart", "graph", "acctitle", "accdescr", "callback", "link"}
_LEFT_ROLES = {"left", "base", "primary"}
_ER_LINK = {"one-to-one": "||--||", "one-to-many": "||--o{", "many-to-one": "}o--||", "many-to-many": "}o--o{",
            "unknown": "}o--o{"}


def _label(value: Any) -> str:
    """Text for inside quotes: letters, digits, spaces and ``. , : $ = -``; anything else becomes ``_``."""
    text = re.sub(r"[^\w .,:$=-]", "_", str(value if value is not None else ""))
    return text[:_LABEL_MAX]


def _ident(value: Any) -> str:
    """A Mermaid identifier: ASCII letters, digits and ``_``, never one of the words Mermaid reads at a line's start."""
    text = re.sub(r"[^A-Za-z0-9_]", "_", str(value if value is not None else ""))[:_LABEL_MAX].strip("_") or "_"
    return f"_{text}" if text.split("_")[0].lower() in _RESERVED else text


# ---------------------------------------------------------------- the answer view

def _equalities(expr: Optional[Dict[str, Any]], left: str, right: str) -> List[Tuple[str, str]]:
    """The ``left.a = right.b`` column pairs of a join condition (through ``AND``), as (a, b)."""
    if not isinstance(expr, dict) or expr.get("kind") != "binary":
        return []
    if expr.get("op") == "AND":
        return _equalities(expr.get("left"), left, right) + _equalities(expr.get("right"), left, right)
    a, b = expr.get("left") or {}, expr.get("right") or {}
    if expr.get("op") != "=" or a.get("kind") != "column" or b.get("kind") != "column":
        return []
    if (a.get("alias"), b.get("alias")) == (left, right):
        return [(a.get("column_name"), b.get("column_name"))]
    if (a.get("alias"), b.get("alias")) == (right, left):
        return [(b.get("column_name"), a.get("column_name"))]
    return []


def _sql_tables(sql: str) -> List[Tuple[Optional[str], str]]:
    """The tables a verified query's SQL reads, as (schema, name); none if it does not parse."""
    try:
        import sqlglot
        from sqlglot import exp

        tree = sqlglot.parse_one(sql)
        ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
        return [(table.db or None, table.name) for table in tree.find_all(exp.Table)
                if table.name and table.name not in ctes]
    except Exception:  # a diagram never fails an answer over SQL it cannot read
        return []


def diagram_for(result: "QueryResult") -> str:
    """The tables ``result`` read, as a Mermaid ``flowchart LR``.

    One node per table, one edge per join labelled with its columns, and a ``subgraph`` per datasource when the
    answer read several, with the combine step's join keys between them. A verified query has no plan: its tables
    are read from its SQL and left unconnected. Only the result's plans are read, never its rows.
    """
    nodes: Dict[Tuple[str, Optional[str], str], str] = {}
    members: Dict[str, List[str]] = {}
    edges: List[Tuple[str, str, str]] = []

    def node(datasource: str, schema: Optional[str], name: str) -> str:
        key = (datasource, schema, name)
        if key not in nodes:
            nodes[key] = f"t{len(nodes)}"
            members.setdefault(datasource, []).append(key)  # type: ignore[arg-type]
        return nodes[key]

    for sub in result.sub_queries:
        datasource = sub.datasource_id or ""
        aliases: Dict[str, str] = {}
        if sub.plan:
            for table in sub.plan.get("tables") or []:
                if table.get("name"):
                    aliases[table.get("alias") or table["name"]] = node(datasource, table.get("schema_name"), table["name"])
            for join in sub.plan.get("joins") or []:
                left, right = aliases.get(join.get("left_alias")), aliases.get(join.get("right_alias"))
                if left and right:
                    pairs = _equalities(join.get("condition"), join["left_alias"], join["right_alias"])
                    edges.append((left, right, ", ".join(f"{a} = {b}" for a, b in pairs)))
        elif sub.sql:
            for schema, name in _sql_tables(sub.sql):
                node(datasource, schema, name)

    names = {key: (f"{key[1]}.{key[2]}" if key[1] else key[2]) for key in nodes}
    several = len(members) > 1
    groups = {datasource: f"g{i}" for i, datasource in enumerate(members)} if several else {}
    by_sub = {sub.id: sub.datasource_id or "" for sub in result.sub_queries}
    for group in (result.decomposition.combine_groups if result.decomposition else []):
        inputs = sorted(group.inputs, key=lambda i: 0 if i.role in _LEFT_ROLES else 1)
        if group.operation in ("join", "compare") and len(inputs) >= 2 and group.join_keys:
            a, b = (groups.get(by_sub.get(i.subquery_id, "")) for i in inputs[:2])
            if a and b and a != b:
                edges.append((a, b, ", ".join(f"{k.left} = {k.right}" for k in group.join_keys)))

    lines = ["flowchart LR"]
    if not nodes:
        lines.append('    none["No tables"]')
    for datasource, keys in members.items():
        indent = "    "
        if several:
            lines.append(f'    subgraph {groups[datasource]}["{_label(datasource)}"]')
            indent = "        "
        lines += [f'{indent}{nodes[key]}["{_label(names[key])}"]' for key in keys]  # type: ignore[index]
        if several:
            lines.append("    end")
    seen: Set[Tuple[str, str, str]] = set()
    for a, b, label in edges:
        if (a, b, label) not in seen:
            seen.add((a, b, label))
            lines.append(f'    {a} ---|"{_label(label)}"| {b}' if label else f"    {a} --- {b}")
    return "\n".join(lines)


# ---------------------------------------------------------------- the schema view

def _parts(name: str) -> Tuple[str, str]:
    """(table, column) of ``table.column`` or ``schema.table.column``; the table part is its last segment."""
    table, _, column = name.strip().rpartition(".")
    return table.rpartition(".")[2].lower(), column


def schema_diagram(view: Dict[str, Any], tables: Optional[Sequence[str]] = None, *, joins: Iterable[Any] = (),
                   max_tables: int = MAX_TABLES) -> str:
    """An ``erDiagram`` of ``view`` (a ``datasources.schema`` result: only what the caller may read).

    Each table is drawn with its primary and foreign key columns; each foreign key and each glossary join in
    ``joins`` between two drawn tables is a relationship. ``tables`` limits the diagram to those tables and the
    ones one relationship away; more than ``max_tables`` is cut to those with the most relationships, and a comment
    counts the rest.

    Raises:
        ValueError: ``tables`` names no table of the view.
    """
    schema_tables: List[Dict[str, Any]] = view.get("tables") or []
    by_name: Dict[str, Dict[str, Any]] = {}
    for table in schema_tables:
        by_name.setdefault(table["name"].lower(), table)
    clashes = {n for n in (t["name"] for t in schema_tables) if sum(t["name"] == n for t in schema_tables) > 1}

    def entity(table: Dict[str, Any]) -> str:
        return _ident(f"{table.get('schema')}_{table['name']}" if table["name"] in clashes else table["name"])

    # (referenced, referencing, link, label), over every table in the view.
    links: List[Tuple[str, str, str, str]] = []
    for table in schema_tables:
        for fk in table.get("foreign_keys") or []:
            target = by_name.get(str(fk["references_table"]).lower())
            if target is not None:
                label = f'{", ".join(fk["columns"])} = {", ".join(fk["references_columns"])}'
                links.append((entity(target), entity(table), "||--o{", label))
    for join in joins:
        sides = [[_parts(n) for n in ([s] if isinstance(s, str) else s)] for s in (join.from_, join.to)]
        source, target = (by_name.get(sides[0][0][0]), by_name.get(sides[1][0][0]))
        if source is not None and target is not None:
            label = f'{", ".join(c for _, c in sides[0])} = {", ".join(c for _, c in sides[1])}'
            links.append((entity(source), entity(target), _ER_LINK.get(join.cardinality, _ER_LINK["unknown"]), label))

    chosen = list(schema_tables)
    if tables:
        wanted = {t.strip().lower() for t in tables}
        asked = {entity(t) for t in schema_tables
                 if t["name"].lower() in wanted or f"{t.get('schema')}.{t['name']}".lower() in wanted}
        if not asked:
            raise ValueError(f"No table of this datasource matches: {', '.join(tables)}.")
        near = asked | {b for a, b, _, _ in links if a in asked} | {a for a, b, _, _ in links if b in asked}
        chosen = [t for t in schema_tables if entity(t) in near]
    degree = {entity(t): 0 for t in chosen}
    for a, b, _, _ in links:
        if a in degree and b in degree:
            degree[a] += 1
            degree[b] += 1
    kept = sorted(chosen, key=lambda t: (-degree[entity(t)], t["name"]))[:max_tables]
    drawn = {entity(t) for t in kept}

    lines = ["erDiagram"]
    for table in sorted(kept, key=lambda t: t["name"]):
        foreign = {c for fk in table.get("foreign_keys") or [] for c in fk["columns"]}
        lines.append(f"    {entity(table)} {{")
        for column in table.get("columns") or []:
            keys = [k for k, on in (("PK", column.get("primary_key")), ("FK", column["name"] in foreign)) if on]
            if keys:
                lines.append(f'        {_ident(column.get("type") or "unknown")} {_ident(column["name"])} {", ".join(keys)}')
        lines.append("    }")
    seen: Set[Tuple[str, str, str, str]] = set()
    for a, b, link, label in links:
        if a in drawn and b in drawn and (a, b, link, label) not in seen:
            seen.add((a, b, link, label))
            lines.append(f'    {a} {link} {b} : "{_label(label)}"')
    if len(chosen) > len(kept):
        lines.append(f"    %% {len(chosen) - len(kept)} more tables not shown")
    return "\n".join(lines)
