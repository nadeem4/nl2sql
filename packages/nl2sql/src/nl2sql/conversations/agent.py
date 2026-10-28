"""The conversation agent: one structured model call per step of a turn (docs/architecture/wave_c.md, section 3).

``block`` renders what the model sees as one JSON object; ``decide`` asks for a ``ModelStep`` and checks it
against the tools that exist, so a step the engine would refuse is sent back by the parse-and-retry of
``structured()`` instead of failing the turn.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from pydantic import BaseModel, Field, ValidationError, create_model, model_validator

from nl2sql.api.query_api import tables_read
from nl2sql.clarify.models import ClarificationRequest
from nl2sql.common.strict import StrictModel
from nl2sql.conversations.models import ModelStep, ToolCall, ToolStep, Turn
from nl2sql.conversations.prompts import CONVERSATION_PROMPT
from nl2sql.llm.wires import structured

AGENT = "conversation"


class AskArgs(StrictModel):
    """The ``ask`` tool's arguments. There is no SQL field: the engine plans, checks and generates."""

    question: str = Field(min_length=1, description="A question that can be answered with no other context.")
    choice: Optional[str] = Field(None, description="An option id of the pending question, when the user picked one.")


ASK_SPEC = {
    "name": "ask",
    "description": "Ask the database one question. The engine routes it, plans it, checks it against the "
                   "caller's permissions and runs it. You get the answer's summary, the columns and the status, "
                   "never the rows.",
    "args_schema": AskArgs.model_json_schema(),
}


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def step_view(step: ToolStep) -> Dict[str, Any]:
    """A step as the model sees it: the call, its status and what it found, never rows."""
    view: Dict[str, Any] = {"tool": step.call.name, "args": step.call.args, "status": step.status}
    if step.question:
        view["question"] = step.question
    result = step.result
    if result is not None:
        view["summary"] = (result.final_answer or {}).get("summary")
        view["columns"] = list(dict.fromkeys(c for m in result.result_manifest for c in m.columns))
        view["datasources"] = sorted({sq.datasource_id for sq in result.sub_queries if sq.datasource_id})
        view["tables"] = tables_read(result)
    if step.error:
        view["error"] = step.error
    return view


def _turn_view(turn: Turn) -> Dict[str, Any]:
    return {"index": turn.index, "kind": turn.kind, "text": turn.text, "reply": turn.reply,
            "status": turn.status, "steps": [step_view(s) for s in turn.steps]}


def block(turns: Sequence[Turn], text: str, *, context_turns: int, pending: Optional[ClarificationRequest],
          tools: Sequence[Mapping[str, Any]], calls_left: int, this_turn: Sequence[ToolStep]) -> str:
    """The human message: ``[CONVERSATION]`` (one JSON object), then ``[USER_TEXT]`` (the text as a JSON string).

    Every piece of earlier text, the new text included, is a JSON string value, so none of it can start a
    line of its own or pass for a marker.
    """
    state = {
        "turns": [_turn_view(t) for t in turns[-context_turns:]],
        "pending": None if pending is None else {
            "question": pending.question, "phrase": pending.phrase,
            "options": [{"id": o.id, "label": o.label} for o in pending.options]},
        "tools": list(tools) if calls_left > 0 else [],
        "tool_calls_left": calls_left,
        "this_turn": [step_view(s) for s in this_turn],
    }
    return f"[CONVERSATION]\n{_dump(state)}\n\n[USER_TEXT]\n{_dump(text)}"


def _check_call(call: ToolCall, tools: Mapping[str, type], option_ids: Sequence[str], *, offered: bool) -> None:
    try:
        if call.name == "ask" and not offered:
            args = AskArgs.model_validate(call.args)
            if args.choice is not None and args.choice not in option_ids:
                shown = ", ".join(option_ids) or "none: nothing is pending"
                raise ValueError(f"'{args.choice}' is not an option of the pending question ({shown}).")
        elif call.name in tools:
            tools[call.name].model_validate(call.args)
        else:
            names = ", ".join(["ask", *tools]) if not offered else ", ".join(tools) or "none"
            raise ValueError(f"'{call.name}' is not {'a tool you may offer' if offered else 'an enabled tool'}; "
                             f"the {'offerable' if offered else 'enabled'} tools are: {names}.")
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc'])) or 'args'}: {e['msg']}" for e in exc.errors())
        raise ValueError(f"The arguments of '{call.name}' are not valid: {problems}.") from None


def checked_step(tools: Mapping[str, type], option_ids: Sequence[str], calls_left: int) -> type:
    """``ModelStep`` with the checks that need the turn's context: tool names, argument schemas, option ids."""

    def _context(self: ModelStep) -> ModelStep:
        if self.call is not None:
            if calls_left <= 0:
                raise ValueError("No tool calls are left in this turn: reply to the user.")
            _check_call(self.call, tools, option_ids, offered=False)
        for offer in self.offers:
            _check_call(offer, tools, option_ids, offered=True)
        return self

    return create_model("ModelStep", __base__=ModelStep,
                        __validators__={"context": model_validator(mode="after")(_context)})


class ConversationAgent:
    """Asks the ``conversation`` agent's model what to do next."""

    def __init__(self, ctx: Any) -> None:
        self._registry = ctx.llm_registry

    def decide(self, message: str, schema: type, callbacks: List[Any]) -> ModelStep:
        """One model call: the step, parsed against ``schema`` (``checked_step``), retried on a miss.

        Raises:
            StructuredOutputError: the answer still did not match after the agent's parse retries.
        """
        chain = CONVERSATION_PROMPT | structured(self._registry.get_llm(AGENT), schema)
        answer: BaseModel = chain.invoke({"message": message}, config={
            "callbacks": callbacks, "metadata": {"langgraph_node": AGENT}})
        return ModelStep.model_validate(answer.model_dump())
