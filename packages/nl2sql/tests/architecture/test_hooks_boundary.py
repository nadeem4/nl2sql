"""Hooks modify only before the validator, and only deny after it (docs/architecture/wave_a.md, section 6).

The invariant *Hooks Modify Before the Validator, Deny After*
(docs/architecture/invariants.md) is held three ways:

1. ``HOOK_POINTS`` allows ``Modify`` at ``run.before``, ``datasources.after``
   and ``plan.after`` only.
2. An AST scan finds every call of the runner (``<...>hooks.call(...)``). Each
   names its point as a literal, so the scan sees which point is called where,
   and no other call passes a point name along (no helper hides a point). A
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


def _runner_method(node: ast.Call):
    """``"call"`` for ``hooks.call(...)`` or ``self.hooks.call(...)``, ``"attached"`` likewise; else None."""
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr in ("call", "attached")):
        return None
    target = func.value
    name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
    return func.attr if name == "hooks" else None


def _call_sites():
    """``{point: {file, ...}}`` for every runner call in the engine, and the problems the scan found."""
    sites, problems = defaultdict(set), []
    for path in SRC.rglob("*.py"):
        file = path.relative_to(SRC).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            first = node.args[0] if node.args else None
            literal = first.value if isinstance(first, ast.Constant) and first.value in HOOK_POINTS else None
            method = _runner_method(node)
            if method == "attached":
                continue
            if method == "call":
                if literal is None:
                    problems.append(f"{file}:{node.lineno}: the runner is called without a literal point")
                else:
                    sites[literal].add(file)
            elif literal is not None:
                problems.append(f"{file}:{node.lineno}: {literal!r} is passed to something other than the runner")
    return sites, problems


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
    sites, problems = _call_sites()
    assert not problems, problems
    assert set(sites) == set(HOOK_POINTS), f"points never called: {set(HOOK_POINTS) - set(sites)}"
    for point in MAY_MODIFY:
        assert sites[point] <= MODIFY_CALLERS, (point, sites[point] - MODIFY_CALLERS)

    # 3. The graph: the planner reaches the generator only through the validator.
    graph = _sql_agent_graph(monkeypatch)
    edges = defaultdict(list)
    for edge in graph.edges:
        edges[edge.source].append(edge.target)
    assert "generator" in _reachable(edges, "ast_planner", removed=None)
    assert "generator" not in _reachable(edges, "ast_planner", removed="logical_validator")
