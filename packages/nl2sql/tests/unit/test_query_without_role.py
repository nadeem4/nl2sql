"""A question asked through the SDK with no ``user_context`` runs, it does not crash.

``NL2SQL.run_query`` defaults ``user_context`` to None and passed it straight to
``GraphState``, whose field is a ``UserContext``: pydantic raised before the
graph started. No role now means an empty ``UserContext``, the same state a run
starts from when the field is left out, so RBAC sees a caller with no roles
exactly as it already did. No authorisation decision changes.
"""
from __future__ import annotations

from types import SimpleNamespace

from nl2sql import NL2SQL, QueryResult, UserContext
from nl2sql.api.query_api import QueryAPI
from nl2sql.pipeline import runtime


class _FakeGraph:
    def __init__(self):
        self.states = []

    def invoke(self, state, config=None):
        self.states.append(state)
        return {"final_answer": "ok"}


def _engine(monkeypatch):
    graph = _FakeGraph()
    monkeypatch.setattr(runtime, "build_graph", lambda ctx, execute=True: graph)
    engine = NL2SQL.__new__(NL2SQL)
    engine.query = QueryAPI(SimpleNamespace())
    return engine, graph


def test_the_sdk_runs_a_question_with_no_user_context(monkeypatch):
    engine, graph = _engine(monkeypatch)

    result = engine.run_query("How many customers are there?")

    assert isinstance(result, QueryResult)
    [state] = graph.states
    assert state["user_context"] == UserContext().model_dump()


def test_the_sdk_passes_a_given_user_context_through(monkeypatch):
    engine, graph = _engine(monkeypatch)

    engine.run_query("q", user_context=UserContext(roles=["analyst"]))

    assert graph.states[0]["user_context"]["roles"] == ["analyst"]
