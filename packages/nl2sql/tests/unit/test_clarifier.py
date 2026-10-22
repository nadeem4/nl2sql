"""Clarify instead of guess: the clarifier and the result (docs/architecture/wave_b.md, section 7; D47, L1).

The logical validator's "valid" edge goes to the clarifier. It validates each
of the planner's alternatives with the validator's own checks (the caller's
scope included), drops the ones that fail, merges the ones that render to the
same SQL, and, when two or three options remain for the run's only sub-query,
ends the sub-query with ``NEEDS_CLARIFICATION`` before the generator: nothing
is executed. Otherwise the default plan runs and the alternatives left are
listed as assumptions in the explanation.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from test_plan_cache import _artifact, _executed

from nl2sql.clarify.models import ClarificationRequest
from nl2sql.common.errors import ErrorCode, ErrorSeverity
from nl2sql.events import EventBus, RunScope
from nl2sql.explain.render import explain_plan
from nl2sql.pipeline.graph_utils import build_scan_payload, wrap_subgraph
from nl2sql.pipeline.nodes.ast_planner.schemas import ASTPlannerResponse, PlanAlternative, PlanModel
from nl2sql.pipeline.nodes.decomposer.schemas import CombineGroup, CombineInput, DecomposerResponse, SubQuery
from nl2sql.pipeline.nodes.schema_retriever.schema import Column, Table
from nl2sql.pipeline.state import GraphState, SubgraphExecutionState
from nl2sql.policies import UserContext
from nl2sql.testing.runtime import fake_runtime, stub_ds_registry

QUESTION = "What were sales last year?"
ANA = UserContext(user_id="ana", tenant_id="acme", roles=["user"])
TABLES = [Table(name="invoice", columns=[Column(name="id", type="int"), Column(name="total", type="real"),
                                         Column(name="amount", type="real")]),
          Table(name="salaries", columns=[Column(name="id", type="int"), Column(name="pay", type="real")])]
POLICIES = {"roles": {"user": {"description": "d", "allow": ["ds1.main.invoice"]}}}


def _sum(table: str, alias: str, column: str, reasoning: str = "") -> PlanModel:
    return PlanModel.model_validate({
        "tables": [{"name": table, "alias": alias}],
        "select_items": [{"alias": "sales", "expr": {
            "kind": "func", "func_name": "SUM", "is_aggregate": True,
            "args": [{"kind": "column", "alias": alias, "column_name": column}]}}],
        "reasoning": reasoning or None,
    })


GROSS = _sum("invoice", "i", "total", "Sales read as the invoice total.")
INVOICED = PlanAlternative(label="Invoiced amount", plan=_sum("invoice", "i", "amount"))
# The default plan again, with other words: the same SQL.
SAME_SQL = PlanAlternative(label="Gross sales", plan=_sum("invoice", "i", "total", "Another note."))
DENIED = PlanAlternative(label="Payroll", plan=_sum("salaries", "s", "pay"))
BROKEN = PlanAlternative(label="Net", plan=_sum("invoice", "i", "no_such_column"))


def _sq(sq_id: str = "sq1") -> SubQuery:
    return SubQuery(id=sq_id, datasource_id="ds1", intent=QUESTION, schema_version="v1")


def _decomposition(*ids: str) -> DecomposerResponse:
    ids = ids or ("sq1",)
    return DecomposerResponse(
        sub_queries=[_sq(i) for i in ids],
        combine_groups=[CombineGroup(group_id=f"g{n}", operation="standalone",
                                     inputs=[CombineInput(subquery_id=i)]) for n, i in enumerate(ids)])


def _ctx(settings=None, bus=None):
    adapter = SimpleNamespace(get_dialect=lambda: "sqlite")
    llm_registry = MagicMock()
    llm_registry.effective.return_value = None
    return fake_runtime(ds_registry=stub_ds_registry(adapter), policies=POLICIES, llm_registry=llm_registry,
                        settings=settings, **({"bus": bus} if bus is not None else {}))


def _agent(monkeypatch, alternatives, *, phrase="sales", settings=None):
    """The SQL agent with the real validator, clarifier and generator; the planner and executor are fakes.

    Returns ``(graph, planned, executed)``: what the planner and the executor were called with.
    """
    from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph

    planned, executed = [], []
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.SchemaRetrieverNode",
                        lambda _ctx: (lambda s: {"relevant_tables": TABLES}))
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.ASTPlannerNode", lambda _ctx: (
        lambda s: planned.append(s) or {"ast_planner_response": ASTPlannerResponse(
            plan=GROSS, ambiguous_phrase=phrase, alternatives=list(alternatives))}))
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.ExecutorNode", lambda _ctx: (
        lambda s: executed.append(s) or {"executor_response": _executed(_artifact())}))
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.RefinerNode", lambda _ctx: (lambda s: {}))
    return build_sql_agent_graph(_ctx(settings), execute=True), planned, executed


def _sub_state(clarify=True) -> SubgraphExecutionState:
    return SubgraphExecutionState(trace_id="t", user_query=QUESTION, sub_query=_sq(), user_context=ANA,
                                  clarify=clarify)


def _wrapped(monkeypatch, alternatives, *, decomposition=None, clarify=True, phrase="sales", settings=None):
    """The SQL agent wrapped as the main graph runs it: the parent graph's update for the sub-query."""
    graph, planned, executed = _agent(monkeypatch, alternatives, phrase=phrase, settings=settings)
    decomposition = decomposition or _decomposition()
    out = wrap_subgraph(graph, "sql_agent", _ctx(settings))({
        "trace_id": "t", "subgraph_id": "sql_agent:sq1:t", "user_query": QUESTION, "user_context": ANA,
        "decomposer_response": decomposition, "clarify": clarify})
    return out, planned, executed


def _result(out, decomposition=None, question=QUESTION):
    from nl2sql.api.query_api import result_from_state

    state = {"trace_id": "t", "user_query": QUESTION, "user_context": ANA,
             "decomposer_response": decomposition or _decomposition(), **out}
    return result_from_state(state, question=question)


# ---------------------------------------------------------------- the clarifier in the SQL agent


def test_two_valid_options_end_the_run_with_a_request(monkeypatch):
    out, _, executed = _wrapped(monkeypatch, [INVOICED])
    result = _result(out)

    assert executed == []
    assert result.status == "needs_clarification"
    request = result.clarification
    assert isinstance(request, ClarificationRequest)
    assert request.question == QUESTION and request.phrase == "sales"
    assert (request.asker_user_id, request.asker_tenant_id) == ("ana", "acme")
    assert request.datasource_id == "ds1" and request.schema_version == "v1"
    assert request.sub_query["id"] == "sq1" and request.knowledge_digest
    assert [o.id for o in request.options] == ["a", "b"]
    default, invoiced = request.options
    assert default.plan == GROSS.model_dump(mode="json") and invoiced.label == "Invoiced amount"
    assert invoiced.plan == INVOICED.plan.model_dump(mode="json")
    # Each option's explanation is X1's rendering of its plan.
    assert any("amount" in m for m in invoiced.explanation.measures)
    assert invoiced.explanation == explain_plan(INVOICED.plan, datasource_id="ds1", sub_query_id="sq1")
    assert default.label and len(default.label) <= 60
    # The answer is a fixed sentence naming the options; the error is INFO and never retried.
    assert result.final_answer["content"].startswith("This question can be read more than one way. Choose one:")
    assert "Invoiced amount" in result.final_answer["content"]
    [error] = result.errors
    assert error["error_code"] == ErrorCode.NEEDS_CLARIFICATION.value
    assert error["severity"] == ErrorSeverity.INFO.value
    # A step that did not run is not explained (X1): the options carry the explanations.
    assert result.explanation is None
    assert result.sub_queries[0].status == "needs_clarification" and result.sub_queries[0].sql == ""


def test_needs_clarification_executes_nothing(monkeypatch):
    graph, planned, executed = _agent(monkeypatch, [INVOICED])

    state = graph.invoke(_sub_state())

    assert executed == [] and state.get("generator_response") is None
    [error] = state["errors"]
    assert error.error_code == ErrorCode.NEEDS_CLARIFICATION and not error.is_retryable
    assert state["retry_count"] == 0 and len(planned) == 1


def test_invalid_alternative_is_dropped(monkeypatch):
    out, _, executed = _wrapped(monkeypatch, [DENIED, INVOICED])
    result = _result(out)

    assert executed == []
    assert [o.label for o in result.clarification.options[1:]] == ["Invoiced amount"]
    # A denied table is never named in the request, nor the option that read it.
    dumped = result.clarification.model_dump_json()
    assert "salaries" not in dumped and "Payroll" not in dumped


def test_one_valid_option_runs_the_default(monkeypatch):
    out, _, executed = _wrapped(monkeypatch, [DENIED, BROKEN])
    result = _result(out)

    assert len(executed) == 1
    assert result.status == "success" and result.clarification is None
    [output] = out["subgraph_outputs"].values()
    # Only the options that passed the validator stay on the output: none here.
    assert output.alternatives == []
    assert "Payroll" not in result.model_dump_json() and "salaries" not in result.model_dump_json()


def test_options_with_the_same_sql_are_merged(monkeypatch):
    out, _, executed = _wrapped(monkeypatch, [SAME_SQL])
    result = _result(out)

    assert len(executed) == 1 and result.clarification is None

    out, _, executed = _wrapped(monkeypatch, [SAME_SQL, INVOICED, INVOICED.model_copy(update={"label": "Billed"})])
    result = _result(out)

    assert executed == []
    assert [o.label for o in result.clarification.options[1:]] == ["Invoiced amount"]


def test_more_than_one_sub_query_runs_the_default_and_lists_assumptions(monkeypatch):
    out, _, executed = _wrapped(monkeypatch, [INVOICED], decomposition=_decomposition("sq1", "sq2"))
    result = _result(out, decomposition=_decomposition("sq1", "sq2"))

    assert len(executed) == 1 and result.clarification is None
    [output] = out["subgraph_outputs"].values()
    assert output.status == "success" and [a.label for a in output.alternatives] == ["Invoiced amount"]
    [step] = result.explanation.steps
    assert any("could also mean Invoiced amount" in a for a in step.assumptions)


def test_clarify_false_runs_the_default_with_the_same_planner_input(monkeypatch):
    asked, asked_planned, _ = _wrapped(monkeypatch, [INVOICED], clarify=True)
    out, planned, executed = _wrapped(monkeypatch, [INVOICED], clarify=False)

    assert len(executed) == 1 and _result(out).status == "success"
    # The planner sees the same state either way: clarify only decides whether the clarifier ends the run.
    seen = lambda s: s.model_dump(exclude={"clarify"})  # noqa: E731
    assert [seen(s) for s in planned] == [seen(s) for s in asked_planned]

    # The run's clarify reaches the sub-query in its scan payload.
    run = GraphState(user_query=QUESTION, clarify=False, decomposer_response=_decomposition())
    assert build_scan_payload(run, "sql_agent", "sq1")["clarify"] is False

    out, _, executed = _wrapped(monkeypatch, [INVOICED], settings={"clarify": {"enabled": False}})
    assert len(executed) == 1 and _result(out).clarification is None


def test_clarifier_passes_through_without_alternatives(monkeypatch):
    graph, _, executed = _agent(monkeypatch, [])

    state = graph.invoke(_sub_state())

    assert len(executed) == 1 and state["generator_response"].sql_draft
    assert not state["errors"]


def test_a_phrase_is_needed_to_ask(monkeypatch):
    graph, _, executed = _agent(monkeypatch, [INVOICED], phrase=None)

    graph.invoke(_sub_state())

    assert len(executed) == 1


def test_clarification_requested_is_published(monkeypatch):
    graph, _, _ = _agent(monkeypatch, [INVOICED])
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append, names=["clarification.requested"])
    scope = RunScope(bus, question=QUESTION)
    with scope.entered():
        graph.invoke(_sub_state())

    [event] = seen
    assert event.data == {"sub_query_id": "sq1", "phrase": "sales", "options": 2}


def test_a_question_asked_back_ends_the_run_at_the_layer_router():
    from langgraph.graph import END

    from nl2sql.common.errors import PipelineError
    from nl2sql.pipeline.routes import build_scan_layer_router

    from nl2sql.execution.dag import ExecutionDAG, LogicalNode

    asked = PipelineError(node="clarifier", message="m", severity=ErrorSeverity.INFO,
                          error_code=ErrorCode.NEEDS_CLARIFICATION)
    # A layer still pending: without the question asked back, the router would dispatch it.
    dag = ExecutionDAG(nodes=[LogicalNode(node_id="sq2", kind="scan", inputs=[])], edges=[], layers=[["sq2"]])
    state = GraphState(user_query=QUESTION, decomposer_response=_decomposition("sq1", "sq2"), execution_dag=dag)
    ctx = fake_runtime(ds_registry=stub_ds_registry(None, supports=lambda *_: True))
    route = build_scan_layer_router(ctx)

    assert route(state) != END
    assert route(state.model_copy(update={"errors": [asked]})) == END


# ---------------------------------------------------------------- the request


def test_request_id_is_the_hash_of_its_content(monkeypatch):
    out, _, _ = _wrapped(monkeypatch, [INVOICED])
    request = _result(out).clarification

    assert request.id == request.expected_id() and len(request.id) == 32
    again, _, _ = _wrapped(monkeypatch, [INVOICED])
    assert _result(again).clarification.id == request.id  # the same content, the same id
    assert _result(again, question="Sales in 2012?").clarification.id != request.id
    edited = request.model_copy(update={"phrase": "revenue"})
    assert edited.expected_id() != request.id


# ---------------------------------------------------------------- the validator as a function


def test_validator_check_is_the_nodes_checks_as_a_function():
    from nl2sql.pipeline.nodes.validator.node import LogicalValidatorNode

    validator = LogicalValidatorNode(_ctx())
    state = SubgraphExecutionState(trace_id="t", sub_query=_sq(), user_context=ANA, relevant_tables=TABLES,
                                   ast_planner_response=ASTPlannerResponse(plan=GROSS))

    assert validator.check(GROSS, state) == []
    [denied] = validator.check(DENIED.plan, state)
    assert denied.error_code == ErrorCode.SECURITY_VIOLATION
    assert any(e.severity in (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL) for e in validator.check(BROKEN.plan, state))
    # The state's own plan is untouched.
    assert state.ast_planner_response.plan == GROSS


# ---------------------------------------------------------------- run_query


def test_run_query_takes_clarify_and_the_question_before_run_before(monkeypatch):
    from nl2sql.api.query_api import QueryAPI

    out, _, _ = _wrapped(monkeypatch, [INVOICED])
    seen = {}

    def fake_run(ctx, question, **kwargs):
        seen.update(kwargs)
        # run.before rewrote the question the graph saw; the request keeps the caller's.
        return {"trace_id": "t", "user_query": "rewritten by a hook", "user_context": ANA,
                "decomposer_response": _decomposition(), **out}

    monkeypatch.setattr("nl2sql.api.query_api.run_with_graph", fake_run)
    api = QueryAPI(_ctx(bus=EventBus()))
    monkeypatch.setattr(api, "close_scope", lambda scope, result: None)

    result = api.run_query(QUESTION, user_context=ANA, clarify=False)

    assert seen["clarify"] is False
    assert result.clarification.question == QUESTION


def test_public_run_query_takes_clarify():
    import inspect

    from nl2sql.public_api import NL2SQL

    for method in (NL2SQL.run_query, NL2SQL.stream_query):
        parameter = inspect.signature(method).parameters["clarify"]
        assert parameter.default is True and parameter.kind is inspect.Parameter.KEYWORD_ONLY


# ---------------------------------------------------------------- after review (#339)


def _retrying_agent(monkeypatch):
    """Attempt 1 has no alternatives and its result is flagged JOIN_FAN_OUT; the re-plan reads "sales" two ways."""
    from nl2sql.common.errors import PipelineError
    from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph

    settings = {"pipeline": {"retries": {"max": 2, "base_delay_s": 0, "jitter_s": 0}}}
    answers = [ASTPlannerResponse(plan=GROSS),
               ASTPlannerResponse(plan=GROSS, ambiguous_phrase="sales", alternatives=[INVOICED])]
    executed = []

    def execute(state):
        executed.append(state)
        update = {"executor_response": _executed(_artifact())}
        if len(executed) == 1:
            update["errors"] = [PipelineError(node="executor", message="fan-out", severity=ErrorSeverity.ERROR,
                                              error_code=ErrorCode.JOIN_FAN_OUT, details={"attempt": 0})]
        return update

    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.SchemaRetrieverNode",
                        lambda _ctx: (lambda s: {"relevant_tables": TABLES}))
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.ASTPlannerNode",
                        lambda _ctx: (lambda s: {"ast_planner_response": answers[min(s.retry_count, 1)]}))
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.ExecutorNode", lambda _ctx: execute)
    monkeypatch.setattr("nl2sql.pipeline.subgraphs.sql_agent.RefinerNode", lambda _ctx: (lambda s: {}))
    return build_sql_agent_graph(_ctx(settings), execute=True), executed, settings


def test_no_question_after_an_executed_attempt(monkeypatch):
    graph, executed, settings = _retrying_agent(monkeypatch)

    out = wrap_subgraph(graph, "sql_agent", _ctx(settings))({
        "trace_id": "t", "subgraph_id": "sql_agent:sq1:t", "user_query": QUESTION, "user_context": ANA,
        "decomposer_response": _decomposition(), "clarify": True})
    result = _result(out)

    # The default reading runs again: the rows are the second attempt's, never the flagged first one's.
    assert len(executed) == 2
    assert result.status == "success" and result.clarification is None
    assert ErrorCode.NEEDS_CLARIFICATION.value not in {w.code for w in result.warnings}
    [step] = result.explanation.steps
    assert any("could also mean Invoiced amount" in a for a in step.assumptions)


def test_a_sub_query_that_asked_never_counts_as_succeeded():
    from nl2sql.common.errors import PipelineError
    from nl2sql.pipeline.nodes.generator.schemas import GeneratorResponse

    asked = PipelineError(node="clarifier", message="m", severity=ErrorSeverity.INFO,
                          error_code=ErrorCode.NEEDS_CLARIFICATION)
    earlier = PipelineError(node="logical_validator", message="No such column.", severity=ErrorSeverity.ERROR,
                            error_code=ErrorCode.COLUMN_NOT_FOUND)
    returned = SubgraphExecutionState(
        trace_id="t", sub_query=_sq(), ast_planner_response=ASTPlannerResponse(plan=GROSS),
        generator_response=GeneratorResponse(sql_draft="SELECT 1"), executor_response=_executed(_artifact()),
        errors=[earlier, asked]).model_dump()
    sub = SimpleNamespace(invoke=lambda state, config=None: returned)

    out = wrap_subgraph(sub, "sql_agent", _ctx())({
        "trace_id": "t", "subgraph_id": "sql_agent:sq1:t", "user_query": QUESTION, "user_context": ANA,
        "decomposer_response": _decomposition()})

    [output] = out["subgraph_outputs"].values()
    assert output.status == "needs_clarification"
    assert out["artifact_refs"] == {}
    # Only the question is the sub-query's error; a superseded attempt's error is a warning, as on success.
    assert [e.error_code for e in out["errors"]] == [ErrorCode.NEEDS_CLARIFICATION]
    assert [(w.code, w.detail) for w in out["warnings"]] == [("COLUMN_NOT_FOUND", {"recovered": True})]


def test_the_planners_alternatives_are_still_counted_after_the_filtering(monkeypatch):
    graph, _, _ = _agent(monkeypatch, [DENIED, BROKEN])

    state = graph.invoke(_sub_state())

    response = state["ast_planner_response"]
    assert response.alternatives == []
    # What the plan cache keys "never cache an answer with alternatives" on (D47): the planner gave two.
    assert response.alternatives_offered == 2
    assert ASTPlannerResponse(plan=GROSS, alternatives=[INVOICED]).alternatives_offered == 1
    assert ASTPlannerResponse(plan=GROSS).alternatives_offered == 0


def test_checking_a_denied_alternative_publishes_no_denial(monkeypatch):
    graph, _, _ = _agent(monkeypatch, [DENIED, INVOICED])
    bus = EventBus()
    seen = []
    bus.subscribe(seen.append, names=["policy.decision"])
    scope = RunScope(bus, question=QUESTION)
    with scope.entered():
        graph.invoke(_sub_state())

    assert seen == []


def _engine(monkeypatch, written=None):
    """An engine over a fake runtime whose graph records the state it starts from."""
    from nl2sql import NL2SQL
    from nl2sql.pipeline import runtime

    states = []

    class Graph:
        def invoke(self, state, config=None):
            states.append(state)
            return {}

    monkeypatch.setattr(runtime, "build_graph", lambda ctx, execute=True: Graph())
    if written is not None:
        monkeypatch.setattr(runtime, "write_run_trace", lambda mode, **kwargs: written.append(kwargs))
    engine = NL2SQL.__new__(NL2SQL)
    engine._runtime = _ctx(bus=EventBus())
    return engine, states


def test_stream_query_passes_clarify_to_the_run(monkeypatch):
    engine, states = _engine(monkeypatch)

    events = list(engine.stream_query(QUESTION, user_context=ANA, clarify=False))
    list(engine.stream_query(QUESTION, user_context=ANA))

    assert events[-1].name == "run.finished"
    assert [s["clarify"] for s in states] == [False, True]


def test_the_trace_records_clarify_and_replay_runs_with_it(monkeypatch):
    from nl2sql.pipeline import runtime as pipeline_runtime
    from nl2sql.tracing.replay import replay_trace

    written = []
    engine, _ = _engine(monkeypatch, written)
    engine.run_query(QUESTION, user_context=ANA, clarify=False)
    [trace] = written
    assert trace["request"]["clarify"] is False

    seen = {}
    monkeypatch.setattr(pipeline_runtime, "run_with_graph",
                        lambda ctx, question, **kwargs: seen.update(kwargs) or {"trace_id": "t"})
    replay_trace({"request": {"question": QUESTION, "clarify": False}, "nodes": [], "result": {}},
                 engine._runtime)
    assert seen["clarify"] is False


def test_the_rendered_planner_prompt_does_not_depend_on_clarify():
    from nl2sql.pipeline.nodes.ast_planner.node import ASTPlannerNode
    from nl2sql.pipeline.nodes.ast_planner.prompts import PLANNER_PROMPT

    ctx = _ctx()
    ctx.llm_registry.get_llm.return_value = MagicMock()
    node = ASTPlannerNode(ctx)
    node.chain = MagicMock()
    node.chain.invoke.return_value = GROSS
    prompts = []
    for clarify in (True, False):
        node(SubgraphExecutionState(trace_id="t", user_query=QUESTION, sub_query=_sq(), user_context=ANA,
                                    relevant_tables=TABLES, clarify=clarify))
        prompts.append(PLANNER_PROMPT.format_messages(**node.chain.invoke.call_args.args[0]))

    assert prompts[0] == prompts[1]
