"""Hooks modify only before the validator, and only deny after it (docs/architecture/wave_a.md, section 6).

The invariant *Hooks Modify Before the Validator, Deny After*
(docs/architecture/invariants.md) is held three ways:

1. ``HOOK_POINTS`` allows ``Modify`` at ``run.before``, ``datasources.after``
   and ``plan.after`` only.
2. Every call naming a point (``hooks.call("<point>", ...)``, or a node's
   own helper such as ``self._hook("<point>", ...)``) is found by an AST scan: a
   point that allows ``Modify`` is called only from the run's worker, the
   resolver and the planner, and every point is called somewhere.
3. In the SQL agent graph, built with fakes, every path from the planner to
   the generator passes the logical validator. A node may sit between the
   validator and the generator (Wave B's clarifier); none may bypass the
   validator.
"""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from nl2sql.hooks import HOOK_POINTS, Modify

SRC = Path(__file__).resolve().parents[2] / "src" / "nl2sql"

MAY_MODIFY = {"run.before", "datasources.after", "plan.after"}
# Where a point that may modify is called: all before the validator.
MODIFY_CALLERS = {
    "pipeline/runtime.py",
    "pipeline/nodes/datasource_resolver/node.py",
    "pipeline/nodes/ast_planner/node.py",
}
# After the validator: these may call only points that cannot modify.
AFTER_THE_VALIDATOR = {
    "pipeline/nodes/generator/node.py",
    "pipeline/nodes/executor/node.py",
    "pipeline/nodes/curated_matcher/node.py",
    "pipeline/nodes/aggregator/node.py",
    "aggregation/aggregator.py",
    "pipeline/nodes/answer_synthesizer/node.py",
}


def _call_sites():
    """``{point: {file, ...}}`` for every call in the engine whose first argument names a point."""
    sites = defaultdict(set)
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in HOOK_POINTS):
                sites[node.args[0].value].add(path.relative_to(SRC).as_posix())
    return sites


def _sql_agent_graph(monkeypatch):
    from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph
    from nl2sql.testing.runtime import fake_runtime

    for name in ("SchemaRetrieverNode", "ASTPlannerNode", "LogicalValidatorNode", "RefinerNode", "GeneratorNode",
                 "ExecutorNode"):
        monkeypatch.setattr(f"nl2sql.pipeline.subgraphs.sql_agent.{name}", lambda _ctx: (lambda state: {}))
    return build_sql_agent_graph(fake_runtime(), execute=True).get_graph()


def _reachable(edges, start, removed):
    seen, pending = set(), [start]
    while pending:
        node = pending.pop()
        if node in seen or node == removed:
            continue
        seen.add(node)
        pending.extend(edges.get(node, ()))
    return seen


def test_hooks_modify_only_before_the_validator(monkeypatch):
    # 1. The point table.
    assert {point for point, rule in HOOK_POINTS.items() if Modify in rule.decisions} == MAY_MODIFY

    # 2. The call sites.
    sites = _call_sites()
    assert set(sites) == set(HOOK_POINTS), f"points never called: {set(HOOK_POINTS) - set(sites)}"
    for point in MAY_MODIFY:
        assert sites[point] <= MODIFY_CALLERS, (point, sites[point] - MODIFY_CALLERS)
    for point, files in sites.items():
        if point in MAY_MODIFY:
            assert not files & AFTER_THE_VALIDATOR, (point, files & AFTER_THE_VALIDATOR)

    # 3. The graph: the planner reaches the generator only through the validator.
    graph = _sql_agent_graph(monkeypatch)
    edges = defaultdict(list)
    for edge in graph.edges:
        edges[edge.source].append(edge.target)
    assert "generator" in _reachable(edges, "ast_planner", removed=None)
    assert "generator" not in _reachable(edges, "ast_planner", removed="logical_validator")
