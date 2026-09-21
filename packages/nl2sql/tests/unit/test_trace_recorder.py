"""``TraceRecorder`` sees every node execution, in order, with its LLM calls.

Driven through a real (tiny) LangGraph so the node attribution rests on the
same ``metadata["langgraph_node"]`` LangGraph really sends: a parent graph fans
two sub-queries out to a subgraph whose planner runs twice each (a retry).
"""
from __future__ import annotations

import operator
from typing import Annotated, List, TypedDict

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from nl2sql.services.callbacks.token_handler import TokenUsageCallback
from nl2sql.tracing.recorder import TraceRecorder


def _llm():
    usage = {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
    return FakeMessagesListChatModel(responses=[AIMessage(content="plan-text", usage_metadata=usage)])


class Parent(TypedDict, total=False):
    user_query: str
    out: Annotated[List[str], operator.add]


class Sub(TypedDict, total=False):
    subgraph_id: str
    retry_count: int
    out: Annotated[List[str], operator.add]


def _graph():
    chain = ChatPromptTemplate.from_template("Plan for {q}, try {n}") | _llm()

    def ast_planner(state: Sub):
        text = chain.invoke({"q": state["subgraph_id"].split(":")[1], "n": state.get("retry_count", 0)})
        return {"out": [text.content], "retry_count": state.get("retry_count", 0) + 1}

    sub = StateGraph(Sub)
    sub.add_node("ast_planner", ast_planner)
    sub.set_entry_point("ast_planner")
    sub.add_conditional_edges("ast_planner", lambda s: "again" if s["retry_count"] < 2 else "done",
                              {"again": "ast_planner", "done": END})
    subgraph = sub.compile()

    def decomposer(state: Parent):
        chain.invoke({"q": state["user_query"], "n": 0})
        return {"out": ["decomposed"]}

    def sql_agent(payload, config=None):
        result = subgraph.invoke(payload, config=config)
        return {"out": result["out"]}

    g = StateGraph(Parent)
    g.add_node("decomposer", decomposer)
    g.add_node("sql_agent", sql_agent)
    g.set_entry_point("decomposer")
    g.add_conditional_edges(
        "decomposer",
        lambda s: [Send("sql_agent", {"subgraph_id": f"sql_agent:{sq}:t1"}) for sq in ("sq1", "sq2")],
        ["sql_agent"],
    )
    g.add_edge("sql_agent", END)
    return g.compile()


def _run():
    recorder = TraceRecorder()
    usage = TokenUsageCallback()
    _graph().invoke({"user_query": "how many?"}, config={"callbacks": [usage, recorder]})
    return recorder.node_executions(usage=usage)


def test_every_node_execution_is_recorded_with_sub_query_and_attempt():
    execs = _run()
    keys = [(e["node"], e["sub_query_id"], e["attempt"]) for e in execs]
    assert keys[0] == ("decomposer", None, 1)
    assert sorted(k for k in keys if k[0] == "sql_agent") == [("sql_agent", "sq1", 1), ("sql_agent", "sq2", 1)]
    assert sorted(k for k in keys if k[0] == "ast_planner") == [
        ("ast_planner", "sq1", 1), ("ast_planner", "sq1", 2),
        ("ast_planner", "sq2", 1), ("ast_planner", "sq2", 2),
    ]
    # Sequence numbers follow start order; nested nodes name their parent node.
    assert [e["seq"] for e in execs] == sorted(e["seq"] for e in execs)
    assert all(e["parent"] == "sql_agent" for e in execs if e["node"] == "ast_planner")
    assert all(e["duration_s"] >= 0 and e["status"] == "ok" for e in execs)


def test_llm_calls_carry_messages_raw_response_parsed_result_and_usage():
    execs = _run()
    planner = [e for e in execs if e["node"] == "ast_planner" and e["sub_query_id"] == "sq2"]
    second = next(e for e in planner if e["attempt"] == 2)
    [call] = second["llm_calls"]
    assert call["key"] == {"node": "ast_planner", "sub_query_id": "sq2", "attempt": 2, "call_index": 1}
    assert call["messages"] == [{"role": "user", "content": "Plan for sq2, try 1"}]
    assert call["prompt_sha256"]
    assert call["response"]["content"] == "plan-text"
    assert call["parsed"]["content"] == "plan-text"
    assert call["usage"]["input_tokens"] == 7 and call["usage"]["node"] == "ast_planner"


def test_inputs_are_the_state_fields_the_node_reads_and_outputs_are_its_update():
    execs = _run()
    decomposer = execs[0]
    assert decomposer["inputs"] == {"user_query": "how many?"}
    assert decomposer["outputs"] == {"out": ["decomposed"]}
