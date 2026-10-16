"""Plain-English phrases for a plan's names and expressions (docs/architecture/wave_b.md, section 8).

A table or column is named by its glossary description's first clause when it
has one, else by its name split into words (``InvoiceDate`` on ``Invoice`` is
"Invoice date"). Each ``Expr`` kind, operator and the common functions have a
phrase; a function on ``ALLOWED_FUNCTIONS`` without one is written as its
name. Everything here is a pure function of the plan and the knowledge: no
model is called, and the same input always gives the same words.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List, Mapping, Optional

from nl2sql.pipeline.nodes.ast_planner.functions import AGGREGATE_FUNCTIONS, date_operation
from nl2sql.pipeline.nodes.ast_planner.schemas import Expr
from nl2sql_sdk.knowledge import DatasourceKnowledge

BINARY_PHRASES: Dict[str, str] = {
    "=": "is", "!=": "is not", ">": "is more than", "<": "is less than", ">=": "is at least",
    "<=": "is at most", "+": "plus", "-": "minus", "*": "times", "/": "divided by", "%": "modulo",
    "||": "followed by", "AND": "and", "OR": "or", "LIKE": "matches", "IN": "is one of",
    "IS": "is", "IS NOT": "is not",
}
UNARY_PHRASES: Dict[str, str] = {"NOT": "not", "-": "minus", "+": "plus"}

# A function's phrase from its phrased arguments; "{0}" is the first.
FUNCTION_PHRASES: Dict[str, str] = {
    "SUM": "total of {0}", "AVG": "average of {0}", "MIN": "smallest {0}", "MAX": "largest {0}",
    "COUNT": "number of {0}", "ABS": "absolute value of {0}", "LENGTH": "length of {0}",
    "LOWER": "{0} in lower case", "UPPER": "{0} in upper case",
}
_ARITHMETIC = {"+", "-", "*", "/", "%", "||"}
_LOGICAL = {"AND", "OR"}
_CLAUSE_END = re.compile(r"[.;:,(]")
_WORDS = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def words(name: str) -> List[str]:
    """``InvoiceDate`` as ``["invoice", "date"]``; an acronym keeps its case (``SKU``)."""
    return [w if len(w) > 1 and w.isupper() else w.lower() for w in _WORDS.findall(name or "")]


def first_clause(text: str) -> str:
    """A description's first clause, its first letter lower-cased unless it starts an acronym."""
    clause = _CLAUSE_END.split(text.strip(), maxsplit=1)[0].strip()
    if len(clause) > 1 and clause[0].isupper() and not clause[1].isupper():
        clause = clause[0].lower() + clause[1:]
    return clause


def listed(items: List[str], last: str = "and") -> str:
    """``a``, ``a and b``, ``a, b and c``."""
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} {last} {items[-1]}"


def _lookup(entries: Mapping[str, object], *keys: str) -> Optional[object]:
    lowered = {k.lower(): v for k, v in entries.items()}
    for key in keys:
        if key and key.lower() in lowered:
            return lowered[key.lower()]
    return None


class Names:
    """How a plan's tables and columns are named: by alias, with the datasource's glossary when there is one.

    ``tables`` maps an alias to its table name, ``schemas`` an alias to its
    schema. A table that appears under two aliases is named with its alias
    (``Track (t3)``). ``select_aliases`` maps a select item's alias to the
    words naming it, for an ``ORDER BY revenue`` that refers to it.
    """

    def __init__(self, tables: Mapping[str, str], schemas: Optional[Mapping[str, Optional[str]]] = None,
                 knowledge: Optional[DatasourceKnowledge] = None, select_aliases: Optional[Mapping[str, str]] = None):
        self.tables = dict(tables)
        self.schemas = dict(schemas or {})
        self.knowledge = knowledge
        self.select_aliases = dict(select_aliases or {})
        counts: Dict[str, int] = {}
        for name in self.tables.values():
            counts[name.lower()] = counts.get(name.lower(), 0) + 1
        self._repeated = {name for name, n in counts.items() if n > 1}

    def qualified(self, alias: str) -> str:
        """The table as written: ``schema.table`` when the plan names a schema."""
        schema = self.schemas.get(alias)
        return f"{schema}.{self.tables[alias]}" if schema else self.tables[alias]

    def table(self, alias: str) -> str:
        """The table's name in a sentence, with its alias when the plan reads it twice."""
        name = self.tables.get(alias, alias)
        return f"{name} ({alias})" if name.lower() in self._repeated else name

    def description(self, alias: str) -> Optional[str]:
        """The first clause of the table's glossary description, if it has one."""
        if self.knowledge is None or alias not in self.tables:
            return None
        entry = _lookup(self.knowledge.tables, self.qualified(alias), self.tables[alias])
        text = getattr(entry, "description", None)
        return first_clause(text) if text else None

    def column(self, alias: Optional[str], column: str) -> str:
        if column == "*":
            return "rows"
        if alias is None:
            if column in self.select_aliases:
                return self.select_aliases[column]
            if len(set(self.tables.values())) == 1 and self.tables:
                alias = next(iter(self.tables))
            else:
                return " ".join(words(column))
        if alias not in self.tables:
            return " ".join(words(column))
        described = self._column_description(alias, column)
        if described:
            return described
        table_words, column_words = words(self.tables[alias]), words(column)
        if column_words[:len(table_words)] == [w.lower() for w in table_words] and len(column_words) > len(table_words):
            column_words = column_words[len(table_words):]
        return f"{self.table(alias)} {' '.join(column_words)}"

    def _column_description(self, alias: str, column: str) -> Optional[str]:
        if self.knowledge is None:
            return None
        entry = _lookup(self.knowledge.columns, f"{self.qualified(alias)}.{column}", f"{self.tables[alias]}.{column}")
        text = getattr(entry, "description", None)
        return first_clause(text) if text else None


def _literal(expr: Expr) -> str:
    if expr.is_null or expr.value is None:
        return "empty"
    if isinstance(expr.value, bool):
        return "true" if expr.value else "false"
    if isinstance(expr.value, str):
        return f"'{expr.value}'"
    return str(expr.value)


def _column(expr: Expr, names: Names) -> str:
    return names.column(expr.alias, expr.column_name or "")


def _func(expr: Expr, names: Names) -> str:
    name = (expr.func_name or "").upper()
    # A CASE inside a call is bracketed, so where it ends stays clear.
    args = [f"({phrase(a, names)})" if a.kind == "case" else phrase(a, names) for a in expr.args]
    date = date_operation(expr.func_name, expr.args)
    if date and date[1] and date[2] is not None:
        return f"the {date[1]} of {phrase(date[2], names)}"
    if name in ("TUPLE", "LIST"):
        return ", ".join(args)
    if name == "ROUND" and args:
        return f"{args[0]}, rounded" + (f" to {args[1]} places" if len(args) > 1 else "")
    if name == "COALESCE" and len(args) > 1:
        return f"{args[0]}, or {', or '.join(args[1:])} when empty"
    if name in FUNCTION_PHRASES and len(args) == 1:
        argument = f"different {args[0]}" if expr.distinct else args[0]
        return FUNCTION_PHRASES[name].format(argument)
    inner = ("DISTINCT " if expr.distinct else "") + ", ".join(args)
    return f"{name}({inner})"


def _needs_brackets(parent: str, child: Expr) -> bool:
    if child.kind != "binary" or child.op == parent:
        return False
    if parent in _LOGICAL:
        return child.op in _LOGICAL
    if parent in _ARITHMETIC:
        return child.op in _ARITHMETIC or child.op in _LOGICAL
    return child.op in _LOGICAL


def _operand(parent: str, child: Expr, names: Names) -> str:
    text = phrase(child, names)
    return f"({text})" if _needs_brackets(parent, child) else text


def _binary(expr: Expr, names: Names) -> str:
    op = expr.op or ""
    left = _operand(op, expr.left, names)
    right_expr = expr.right
    if op in ("IS", "IS NOT") and right_expr.kind == "literal" and (right_expr.is_null or right_expr.value is None):
        return f"{left} {'is empty' if op == 'IS' else 'is not empty'}"
    return f"{left} {BINARY_PHRASES[op]} {_operand(op, right_expr, names)}"


def _unary(expr: Expr, names: Names) -> str:
    inner = phrase(expr.expr, names)
    if expr.op == "NOT":
        return f"not ({inner})"
    return f"{UNARY_PHRASES[expr.op]} {inner}"


def _case(expr: Expr, names: Names) -> str:
    parts = [f"{phrase(w.result, names)} when {phrase(w.condition, names)}" for w in expr.whens]
    if expr.else_expr is not None:
        parts.append(f"otherwise {phrase(expr.else_expr, names)}")
    return "depending on: " + "; ".join(parts)


KIND_PHRASES: Dict[str, Callable[[Expr, Names], str]] = {
    "literal": lambda expr, _names: _literal(expr),
    "column": _column,
    "func": _func,
    "binary": _binary,
    "unary": _unary,
    "case": _case,
}


def phrase(expr: Expr, names: Names) -> str:
    """``expr`` in plain English."""
    return KIND_PHRASES[expr.kind](expr, names)


def is_aggregate(expr: Optional[Expr]) -> bool:
    """Whether ``expr`` computes an aggregate anywhere inside it."""
    if expr is None:
        return False
    if expr.kind == "func" and (expr.is_aggregate or (expr.func_name or "").upper() in AGGREGATE_FUNCTIONS):
        return True
    children = [expr.left, expr.right, expr.expr, expr.else_expr, *expr.args]
    children += [part for w in expr.whens for part in (w.condition, w.result)]
    return any(is_aggregate(c) for c in children if c is not None)


def conjuncts(expr: Optional[Expr]) -> List[Expr]:
    """The parts of a top-level AND, in order."""
    if expr is None:
        return []
    if expr.kind == "binary" and expr.op == "AND":
        return conjuncts(expr.left) + conjuncts(expr.right)
    return [expr]
