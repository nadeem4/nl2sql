"""A stand-in engine for the terminal app's pilot tests: canned turns behind the facade's namespaces.

The app only calls the public facade, so these classes answer the same calls (``conversations``, ``results``,
``approvals``, ``knowledge``, ``datasources``) with the real models and no model, database or index behind them.
"""
from __future__ import annotations

import threading

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from nl2sql import (ApprovalRecord, ClarificationOption, ClarificationRequest, Event, ExplainedStep, Explanation,
                    NotAuthorized, QueryResult, ResultPage, ToolCall, ToolOutput, ToolStep, Turn, UserContext)
from nl2sql.api.query_api import RowSample, SubQueryResult
from nl2sql.execution.artifacts.results import ResultSummary
from nl2sql.hooks.approval import ApprovalRequest

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
COLUMNS = ["country", "customers"]
ROWS = [["USA", 13], ["Canada", 8], ["Brazil", 5]]


def result(*, status: str = "success", answer: Optional[str] = "13 customers are in the USA.", **fields: Any) -> QueryResult:
    """An answered run with one sub-query, its SQL, a row sample and a saved result called ``n1``."""
    base = dict(
        status=status, trace_id="t1", final_answer={"summary": answer} if answer else None,
        sub_queries=[SubQueryResult(id="q1", sql="SELECT country, COUNT(*) FROM customer GROUP BY country",
                                    datasource_id="chinook", rows=RowSample(columns=COLUMNS, rows=ROWS[:2], total_rows=3))],
        result_manifest=[ResultSummary(node_id="n1", columns=COLUMNS, row_count=3)],
        explanation=Explanation(steps=[ExplainedStep(datasource_id="chinook", source="generated", tables=["Customer"],
                                                     groupings=["by Country"], measures=["customers: count of rows"],
                                                         text="Counts customers for each country.")]))
    return QueryResult(**{**base, **fields})


def turn(*steps: ToolStep, index: int = 1, text: str = "customers by country", status: str = "answered",
         reply: Optional[str] = "Most customers are in the USA.", **fields: Any) -> Turn:
    return Turn(index=index, at=NOW, kind="message", text=text, reply=reply, steps=list(steps), status=status,
                trace_id="t1", **fields)


def ask(res: Optional[QueryResult] = None, question: str = "customers by country", status: str = "ok") -> ToolStep:
    return ToolStep(call=ToolCall(name="ask", args={"question": question}), status=status, question=question,
                    trace_id="t1", result=res or result())


def chart_step(body: Dict[str, Any], name: str = "chart", kind: str = "chart") -> ToolStep:
    return ToolStep(call=ToolCall(name=name, args={}), status="ok",
                    output=ToolOutput(kind=kind, title="customers by country", body=body))


def clarification() -> ClarificationRequest:
    step = ExplainedStep(datasource_id="chinook", source="chosen", text="Chosen reading.")
    options = [ClarificationOption(id=i, label=label, plan={"tables": []}, explanation=step)
               for i, label in (("a", "Revenue means invoice totals"), ("b", "Revenue means track sales"))]
    return ClarificationRequest.build(question="revenue by country", phrase="revenue", datasource_id="chinook",
                                      schema_version="1", knowledge_digest="d", sub_query={}, options=options)


def approval_request(roles=("admin",)) -> ApprovalRequest:
    return ApprovalRequest.build(question="customers", asker=UserContext(user_id="ana"), hook="review",
                                 datasource_id="chinook", sql="SELECT * FROM customer", reason="Reads customer data",
                                 approver_roles=list(roles))


class FakeConversations:
    """Scripted: each turn-making call pops the next turn of ``script``; every call is recorded in ``calls``."""

    def __init__(self, script: List[Turn]):
        self.script = list(script)
        self.calls: List[tuple] = []
        self.pending: Optional[ClarificationRequest] = None
        self.gate: Optional[threading.Event] = None  # when set, a stream waits for it: a turn that is still running

    def _next(self) -> Turn:
        return self.script.pop(0)

    def start(self, *, user_context, datasource_id=None, dashboard_id=None):
        self.calls.append(("start", datasource_id))
        return SimpleNamespace(id="c1", turns=[], pending=None, title="New conversation", datasource_id=datasource_id)

    def get(self, conversation_id, *, user_context):
        return SimpleNamespace(id=conversation_id, turns=[], pending=self.pending)

    def list(self, *, user_context, limit=20):
        return [SimpleNamespace(id="c1", title="customers by country", updated_at=NOW, turns=1)]

    def stream(self, conversation_id, text=None, *, user_context, choice=None, execute=True, llm_override=None):
        self.calls.append(("stream", text, choice, user_context))
        if self.gate is not None:
            self.gate.wait(10)
        stored = self._next()
        yield Event(name="conversation.step", at=NOW, data={"tool": "ask", "status": "ok", "turn": 1})
        yield Event(name="conversation.turn", at=NOW, data={"stored": stored.model_dump(mode="json")})

    def tool(self, conversation_id, name, args=None, *, user_context):
        self.calls.append(("tool", name, args))
        return self._next()

    def retry(self, conversation_id, *, user_context, approvals=(), llm_override=None):
        self.calls.append(("retry", list(approvals)))
        return self._next()

    def report(self, conversation_id, *, user_context, turns=None, title=None, include_rows=True):
        self.calls.append(("report",))
        return "# Report\n\nThe USA leads."


class FakeEngine:
    def __init__(self, script: List[Turn], *, approver_roles=("admin",)):
        self.config = SimpleNamespace(settings=SimpleNamespace(tenant_id="t"), policies=SimpleNamespace(default_role="analyst"))
        self.conversations = FakeConversations(script)
        self.pages = [
            ResultPage(request_id="t1", result_id="n1", columns=COLUMNS, rows=ROWS[:2], offset=0, limit=2, total_rows=3, next_offset=2),
            ResultPage(request_id="t1", result_id="n1", columns=COLUMNS, rows=ROWS[2:], offset=2, limit=2, total_rows=3, next_offset=None)]
        self.page_calls: List[tuple] = []
        self.granted: List[str] = []
        self.saved_choices: List[Any] = []
        self.approver_roles = set(approver_roles)
        self.results = SimpleNamespace(page=self._page)
        self.approvals = SimpleNamespace(grant=self._grant, list=self._list_approvals)
        self.knowledge = SimpleNamespace(
            save_choice=lambda choice, *, user_context, term=None: self.saved_choices.append(choice))
        self.datasource_calls: List[Any] = []
        self.page_error: Optional[Exception] = None
        self.datasources = SimpleNamespace(list=self._datasources)

    def _datasources(self, *, user_context=None):
        self.datasource_calls.append(user_context)
        return [SimpleNamespace(id="chinook", sample_questions=["How many customers are there?", "Revenue by year?"])]

    def _page(self, run_id, result_id, offset=0, limit=100, *, user_context):
        self.page_calls.append((run_id, result_id, offset, user_context))
        if self.page_error is not None and offset:
            raise self.page_error
        return next(p for p in self.pages if p.offset == offset)

    def _grant(self, request_id, *, by):
        if not self.approver_roles & set(by.roles):
            raise NotAuthorized("Only an approver can grant this.")
        self.granted.append(request_id)
        return ApprovalRecord(id=request_id, status="granted", request=approval_request(), created_at=NOW, updated_at=NOW)

    def _list_approvals(self, *, user_context, status="pending"):
        return [ApprovalRecord(id="r1", status="pending", request=approval_request(), created_at=NOW, updated_at=NOW)]

    def close(self):
        pass
