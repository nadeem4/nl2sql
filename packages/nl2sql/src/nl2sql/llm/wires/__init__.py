"""One adapter per wire type, and the lookups that pick one.

Adding a wire type (Gemini, Bedrock) is one new adapter module implementing
:class:`Wire`, an entry in :data:`WIRES`, and presets naming it in
``nl2sql.llm.registry.PROVIDER_PRESETS``.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from .anthropic import AnthropicWire
from .base import Wire, temperature_error
from .openai import OpenAIWire

WIRES: Dict[str, Wire] = {"openai": OpenAIWire(), "anthropic": AnthropicWire()}

# The client ``metadata`` key ``LLMRegistry`` writes the agent's ``structured_output`` under.
STRUCTURED_OUTPUT_KEY = "nl2sql_structured_output"

# Each ``structured_output`` setting as LangChain's ``with_structured_output`` names the method.
_METHODS = {"tool_calling": "function_calling", "json_schema": "json_schema", "json_mode": "json_mode"}

__all__ = ["STRUCTURED_OUTPUT_KEY", "WIRES", "Wire", "streaming_structured", "structured", "temperature_error",
           "wire_named", "wire_of"]


def wire_named(name: Optional[str]) -> Wire:
    """The adapter for a wire type or LangChain ``ls_provider``; OpenAI's for anything else."""
    return WIRES.get(name or "", WIRES["openai"])


def wire_of(llm: Any) -> Wire:
    """The adapter for the client ``llm`` actually is; OpenAI's for anything unknown."""
    llm_type = getattr(llm, "_llm_type", None)
    return next((wire for wire in WIRES.values() if wire.llm_type == llm_type), WIRES["openai"])


def _method(llm: Any) -> str:
    """The agent's ``structured_output`` (set on the client when it was built), else the wire's own."""
    metadata = getattr(llm, "metadata", None)
    chosen = metadata.get(STRUCTURED_OUTPUT_KEY) if isinstance(metadata, dict) else None
    return _METHODS.get(chosen) or wire_of(llm).structured_output_method


def _schema_text(schema: Any) -> str:
    return json.dumps(schema.model_json_schema() if hasattr(schema, "model_json_schema") else schema)


def _with_schema(schema: Any) -> Any:
    """A step putting ``schema`` into the prompt: JSON mode alone asks for JSON, not for which JSON."""
    from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
    from langchain_core.runnables import RunnableLambda

    instruction = SystemMessage(content="Answer with only a JSON object that matches this JSON schema:\n"
                                        + _schema_text(schema))

    def add(prompt: Any) -> List[BaseMessage]:
        if hasattr(prompt, "to_messages"):
            messages = prompt.to_messages()
        elif isinstance(prompt, str):
            messages = [HumanMessage(content=prompt)]
        else:
            messages = list(prompt)
        # After the leading system messages, so a provider that wants them first still has them first.
        lead = next((i for i, m in enumerate(messages) if not isinstance(m, SystemMessage)), len(messages))
        return [*messages[:lead], instruction, *messages[lead:]]

    return RunnableLambda(add)


def structured(llm: Any, schema: Any) -> Any:
    """``llm.with_structured_output(schema)`` with the agent's ``structured_output`` method.

    For ``json_mode`` the schema is added to the prompt, since JSON mode alone
    does not carry it.
    """
    method = _method(llm)
    chain = llm.with_structured_output(schema, method=method)
    return _with_schema(schema) | chain if method == "json_mode" else chain


def streaming_structured(llm: Any, schema: Any) -> Any:
    """:func:`structured`, with the provider asked to stream its answer.

    The call is the same request with ``stream`` on (and, on the OpenAI wire,
    the usage asked for at the end of the stream), so each piece of the tool
    call reaches the callbacks' ``on_llm_new_token`` as the model writes it.
    The pieces are joined into one message before the parser sees it, so the
    answer is the one :func:`structured` returns. A wire that repairs tool
    calls (Anthropic's) repairs the joined message too: its own ``_generate``
    hook is not on the streaming path.
    """
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.runnables import RunnableLambda

    method = _method(llm)
    chain = llm.with_structured_output(schema, method=method)
    model, parser = chain.first, chain.last
    tools = getattr(model, "kwargs", {}).get("tools")
    repair = getattr(wire_of(llm), "repair_tool_calls", None)

    def repaired(message: Any) -> Any:
        if repair is None:
            return message
        return repair(ChatResult(generations=[ChatGeneration(message=message)]), tools).generations[0].message

    streamed = model.bind(stream=True, stream_usage=True) | RunnableLambda(repaired) | parser
    return _with_schema(schema) | streamed if method == "json_mode" else streamed
