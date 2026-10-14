"""One adapter per wire type, and the lookups that pick one.

Adding a wire type (Gemini, Bedrock) is one new adapter module implementing
:class:`Wire`, an entry in :data:`WIRES`, and presets naming it in
``nl2sql.llm.registry.PROVIDER_PRESETS``.

Every structured call goes through :func:`structured` (or
:func:`streaming_structured`), which asks with the agent's
``structured_output`` method and retries an answer that does not parse.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .anthropic import AnthropicWire
from .base import Wire, temperature_error
from .openai import OpenAIWire

WIRES: Dict[str, Wire] = {"openai": OpenAIWire(), "anthropic": AnthropicWire()}

# The client ``metadata`` keys ``LLMRegistry`` writes the agent's settings under.
STRUCTURED_OUTPUT_KEY = "nl2sql_structured_output"
PARSE_RETRIES_KEY = "nl2sql_parse_retries"
AGENT_KEY = "nl2sql_agent"

# A client that does not say (one built outside ``LLMRegistry``) retries once, the config's default.
DEFAULT_PARSE_RETRIES = 1

# Each ``structured_output`` setting as LangChain's ``with_structured_output`` names the method.
_METHODS = {"tool_calling": "function_calling", "json_schema": "json_schema", "json_mode": "json_mode"}

__all__ = ["AGENT_KEY", "PARSE_RETRIES_KEY", "STRUCTURED_OUTPUT_KEY", "WIRES", "StructuredOutputError", "Wire",
           "streaming_structured", "structured", "temperature_error", "wire_named", "wire_of"]


class StructuredOutputError(ValueError):
    """The model's structured answer still did not parse after the agent's ``parse_retries``.

    ``provider_error()`` reports it as ``MODEL_OUTPUT_INVALID``.

    Attributes:
        agent: The agent that asked: the node running the call, else the client's agent.
        schema: The name of the schema the answer had to match.
        attempts: How many answers were asked for, the first one included.
        errors: What was wrong with the last answer.
    """

    def __init__(self, agent: str, schema: str, attempts: int, errors: str):
        self.agent, self.schema, self.attempts, self.errors = agent, schema, attempts, errors
        plural = "" if attempts == 1 else "s"
        super().__init__(f"The {agent} agent's answer did not match {schema} after {attempts} attempt{plural}: "
                         f"{errors}")


def wire_named(name: Optional[str]) -> Wire:
    """The adapter for a wire type or LangChain ``ls_provider``; OpenAI's for anything else."""
    return WIRES.get(name or "", WIRES["openai"])


def wire_of(llm: Any) -> Wire:
    """The adapter for the client ``llm`` actually is; OpenAI's for anything unknown."""
    llm_type = getattr(llm, "_llm_type", None)
    return next((wire for wire in WIRES.values() if wire.llm_type == llm_type), WIRES["openai"])


def _metadata(llm: Any) -> Dict[str, Any]:
    metadata = getattr(llm, "metadata", None)
    return metadata if isinstance(metadata, dict) else {}


def _method(llm: Any) -> str:
    """The agent's ``structured_output`` (set on the client when it was built), else the wire's own."""
    return _METHODS.get(_metadata(llm).get(STRUCTURED_OUTPUT_KEY)) or wire_of(llm).structured_output_method


def _schema_name(schema: Any) -> str:
    if isinstance(schema, dict):
        return str(schema.get("title") or "the schema")
    return getattr(schema, "__name__", "the schema")


def _schema_text(schema: Any) -> str:
    return json.dumps(schema.model_json_schema() if hasattr(schema, "model_json_schema") else schema)


def _messages(prompt: Any) -> List[Any]:
    from langchain_core.messages import HumanMessage

    if hasattr(prompt, "to_messages"):
        return prompt.to_messages()
    if isinstance(prompt, str):
        return [HumanMessage(content=prompt)]
    return list(prompt)


def _with_schema(messages: List[Any], schema: Any) -> List[Any]:
    """``messages`` with ``schema`` in the prompt: JSON mode alone asks for JSON, not for which JSON."""
    from langchain_core.messages import SystemMessage

    instruction = SystemMessage(content="Answer with only a JSON object that matches this JSON schema:\n"
                                        + _schema_text(schema))
    # After the leading system messages, so a provider that wants them first still has them first.
    lead = next((i for i, m in enumerate(messages) if not isinstance(m, SystemMessage)), len(messages))
    return [*messages[:lead], instruction, *messages[lead:]]


def _answer_text(message: Any) -> str:
    """What the model answered, as text: a tool call's arguments, else its words."""
    calls = getattr(message, "tool_calls", None) or []
    if calls:
        return json.dumps(calls[0].get("args"))
    invalid = getattr(message, "invalid_tool_calls", None) or []
    if invalid:
        return str(invalid[0].get("args") or "")
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    return str(content)


def _location(loc: Any) -> str:
    """A pydantic error location as a path: ``("steps", 0, "op")`` is ``steps[0].op``."""
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "(root)"


def _errors_of(exc: BaseException) -> str:
    """The validation errors behind a parse failure, one ``path: message`` each."""
    from pydantic import ValidationError

    seen: Optional[BaseException] = exc
    while seen is not None:
        if isinstance(seen, ValidationError):
            return "; ".join(f"`{_location(e['loc'])}`: {e['msg']}" for e in seen.errors())
        seen = seen.__cause__ or seen.__context__
    return str(exc)


def _retrying(llm: Any, schema: Any, model: Any, parser: Any, json_mode: bool,
              streamed: Optional[tuple] = None) -> Any:
    """Asks ``model``, then again with what was wrong, up to the agent's ``parse_retries`` times.

    ``streamed`` is ``(streaming model, repair)``: the first answer is asked
    of that model and passed through ``repair``. Each retry adds the model's
    answer and the errors to the messages. The model and the parser run under
    the caller's config, directly under this step, so every call reaches the
    usage and trace callbacks and the trace records this step's parsed answer
    against the call.
    """
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.runnables import RunnableLambda

    retries = int(_metadata(llm).get(PARSE_RETRIES_KEY, DEFAULT_PARSE_RETRIES))
    name = _schema_name(schema)

    def ask(prompt: Any, config: Any) -> Any:
        messages = _messages(prompt)
        if json_mode:
            messages = _with_schema(messages, schema)
        errors = ""
        for attempt in range(retries + 1):
            if attempt == 0 and streamed is not None:
                answer = streamed[1](streamed[0].invoke(messages, config))
            else:  # a retry is never streamed
                answer = model.invoke(messages, config)
            try:
                parsed = parser.invoke(answer, config)
            except ValueError as exc:  # OutputParserException, ValidationError and JSONDecodeError all are
                parsed, errors = None, _errors_of(exc)
            else:
                errors = "" if parsed is not None else "no structured answer was given"
            if not errors:
                return parsed
            messages = [*messages, AIMessage(content=_answer_text(answer)),
                        HumanMessage(content=f"Your answer did not match the schema: {errors}. "
                                             f"Answer again with only {name}.")]
        agent = (config.get("metadata") or {}).get("langgraph_node") or _metadata(llm).get(AGENT_KEY) or "default"
        raise StructuredOutputError(agent, name, retries + 1, errors)

    return RunnableLambda(ask, name=f"structured_{name}")


def _split(chain: Any) -> Optional[tuple]:
    """``(model, parser)`` of what ``with_structured_output`` returned, or None when it is not that pair."""
    from langchain_core.runnables import RunnableSequence

    if isinstance(chain, RunnableSequence) and len(chain.steps) == 2:
        return chain.first, chain.last
    return None


def structured(llm: Any, schema: Any) -> Any:
    """``llm.with_structured_output(schema)`` with the agent's ``structured_output`` method, and parse-and-retry.

    For ``json_mode`` the schema is added to the prompt, since JSON mode alone
    does not carry it. An answer that does not parse (a parser error, a
    validation error, or no answer at all) is sent back with what was wrong,
    up to the agent's ``parse_retries`` times, then :class:`StructuredOutputError`
    is raised. Each retry is a model call of its own, so it is counted in the
    usage and the trace. The method is never switched automatically.
    """
    method = _method(llm)
    chain = llm.with_structured_output(schema, method=method)
    pair = _split(chain)
    if pair is None:  # not a model and a parser: nothing to send an answer back to
        return chain
    model, parser = pair
    return _retrying(llm, schema, model, parser, method == "json_mode")


def streaming_structured(llm: Any, schema: Any) -> Any:
    """:func:`structured`, with the provider asked to stream its answer.

    The call is the same request with ``stream`` on (and, on the OpenAI wire,
    the usage asked for at the end of the stream), so each piece of the tool
    call reaches the callbacks' ``on_llm_new_token`` as the model writes it.
    The pieces are joined into one message before the parser sees it, so the
    answer is the one :func:`structured` returns. A wire that repairs tool
    calls (Anthropic's) repairs the joined message too: its own ``_generate``
    hook is not on the streaming path. A streamed answer that does not parse
    is retried without streaming.
    """
    from langchain_core.outputs import ChatGeneration, ChatResult

    method = _method(llm)
    chain = llm.with_structured_output(schema, method=method)
    model, parser = chain.first, chain.last
    tools = getattr(model, "kwargs", {}).get("tools")
    repair = getattr(wire_of(llm), "repair_tool_calls", None)

    def repaired(message: Any) -> Any:
        if repair is None:
            return message
        return repair(ChatResult(generations=[ChatGeneration(message=message)]), tools).generations[0].message

    streaming = model.bind(stream=True, stream_usage=True)
    return _retrying(llm, schema, model, parser, method == "json_mode", streamed=(streaming, repaired))
