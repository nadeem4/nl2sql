"""What a rejected join is told: the joins that are allowed between its two tables (docs/architecture/wave_c.md, section 18.3).

A model that is only told "Join does not match any allowed relationship" invents another shortcut on a
shared key. The relationships of the retrieved schema are a graph; the hint names the declared joins
between the two tables or, when there are none, the shortest declared path through the tables between them.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

# One declared join: (table, its columns, the other table, its columns), names normalized.
Edge = Tuple[str, List[str], str, List[str]]


def _edges(relationships: List[Dict[str, Any]], normalize: Callable[[Optional[str]], str]) -> List[Edge]:
    edges: List[Edge] = []
    for rel in relationships:
        a, b = normalize(rel.get("from_table")), normalize(rel.get("to_table"))
        a_cols = [normalize(c) for c in rel.get("from_columns") or []]
        b_cols = [normalize(c) for c in rel.get("to_columns") or []]
        if a and b and a_cols and len(a_cols) == len(b_cols):
            edges.append((a, a_cols, b, b_cols))
    return edges


def _step(edge: Edge, start: str) -> str:
    """The edge as an equality, written from ``start`` outwards."""
    a, a_cols, b, b_cols = edge
    if start != a:
        a, a_cols, b, b_cols = b, b_cols, a, a_cols
    return " and ".join(f"{a}.{x} = {b}.{y}" for x, y in zip(a_cols, b_cols))


def _shortest_path(edges: List[Edge], start: str, goal: str) -> List[str]:
    """The steps of the shortest chain of declared joins from ``start`` to ``goal``; empty when there is none."""
    reached: Dict[str, Optional[Tuple[str, Edge]]] = {start: None}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        if node == goal:
            break
        for edge in sorted(edges, key=lambda e: (e[0], e[2], e[1], e[3])):
            for here, there in ((edge[0], edge[2]), (edge[2], edge[0])):
                if here == node and there not in reached:
                    reached[there] = (node, edge)
                    queue.append(there)
    steps: List[str] = []
    node = goal
    while goal in reached and reached[node] is not None:
        previous, edge = reached[node]
        steps.append(_step(edge, previous))
        node = previous
    return steps[::-1]


def join_hint(relationships: List[Dict[str, Any]], left: str, right: str, normalize: Callable[[Optional[str]], str],
              sent: Set[str]) -> str:
    """The sentence a rejected join between the tables ``left`` and ``right`` is followed by.

    ``sent`` is the tables the planner was sent: a path only runs through those, since the planner can use no other.
    """
    edges = [e for e in _edges(relationships, normalize) if e[0] in sent and e[2] in sent]
    direct = sorted({_step(e, left) for e in edges if {e[0], e[2]} == {left, right}})
    if direct:
        return f"Allowed joins between {left} and {right}: {'; '.join(direct)}."
    none = f"No relationship is declared between {left} and {right}."
    path = _shortest_path(edges, left, right) if left != right else []
    if path:
        return f"{none} Shortest declared path: {'; '.join(path)}."
    return f"{none} No path of declared relationships connects them in the schema you were given."
