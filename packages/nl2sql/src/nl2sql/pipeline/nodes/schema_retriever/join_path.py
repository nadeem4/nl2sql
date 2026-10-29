"""Join-path fill: the tables that connect the ones already chosen (docs/architecture/wave_c.md, section 17.3).

The chosen tables are the nodes to connect in a graph of the tables the caller
may read, whose edges are foreign keys and glossary joins. It is an
approximate Steiner tree: repeatedly take the two nearest unconnected groups
and join them by a shortest path. A path longer than ``max_hops`` is not
added, and the tables stay apart.
"""
from __future__ import annotations

from collections import deque
from typing import Dict, Iterable, List, Optional, Set

Adjacency = Dict[str, Set[str]]


def _path_to_another_group(adjacency: Adjacency, groups: List[Set[str]], index: int) -> Optional[List[str]]:
    """The shortest path from ``groups[index]`` to a node of any other group, ends included; None when there is none."""
    elsewhere = {node: i for i, group in enumerate(groups) if i != index for node in group}
    parents: Dict[str, Optional[str]] = {node: None for node in sorted(groups[index])}
    queue = deque(parents)
    while queue:
        node = queue.popleft()
        if node in elsewhere:
            path = []
            while node is not None:
                path.append(node)
                node = parents[node]
            return path[::-1]
        for neighbour in sorted(adjacency.get(node, ())):
            if neighbour not in parents:
                parents[neighbour] = node
                queue.append(neighbour)
    return None


def connect(adjacency: Adjacency, chosen: Iterable[str], max_hops: int) -> List[str]:
    """The tables, not already chosen, on the paths that join ``chosen`` as far as ``max_hops`` allows, in order."""
    groups = [{node} for node in sorted(set(chosen))]
    added: List[str] = []
    while len(groups) > 1:
        best: Optional[List[str]] = None
        for index in range(len(groups)):
            path = _path_to_another_group(adjacency, groups, index)
            if path is not None and (best is None or len(path) < len(best)):
                best = path
        if best is None or len(best) - 1 > max_hops:
            break
        joined = {i for i, group in enumerate(groups) if group & {best[0], best[-1]}}
        added += [node for node in best if not any(node in group for group in groups)]
        merged = set(best).union(*(groups[i] for i in joined))
        groups = [group for i, group in enumerate(groups) if i not in joined] + [merged]
    return added
