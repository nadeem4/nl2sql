"""One adapter per wire type, and the lookups that pick one.

Adding a wire type (Gemini, Bedrock) is one new adapter module implementing
:class:`Wire`, an entry in :data:`WIRES`, and presets naming it in
``nl2sql.llm.registry.PROVIDER_PRESETS``.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .anthropic import AnthropicWire
from .base import Wire, temperature_error
from .openai import OpenAIWire

WIRES: Dict[str, Wire] = {"openai": OpenAIWire(), "anthropic": AnthropicWire()}

__all__ = ["WIRES", "Wire", "streaming_structured", "structured", "temperature_error", "wire_named",
           "wire_of"]


def wire_named(name: Optional[str]) -> Wire:
    """The adapter for a wire type or LangChain ``ls_provider``; OpenAI's for anything else."""
    return WIRES.get(name or "", WIRES["openai"])


def wire_of(llm: Any) -> Wire:
    """The adapter for the client ``llm`` actually is; OpenAI's for anything unknown."""
    llm_type = getattr(llm, "_llm_type", None)
    return next((wire for wire in WIRES.values() if wire.llm_type == llm_type), WIRES["openai"])


def structured(llm: Any, schema: Any) -> Any:
    """``llm.with_structured_output(schema)`` with the method its wire uses."""
    return llm.with_structured_output(schema, method=wire_of(llm).structured_output_method)


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

    chain = structured(llm, schema)
    model, parser = chain.first, chain.last
    tools = getattr(model, "kwargs", {}).get("tools")
    repair = getattr(wire_of(llm), "repair_tool_calls", None)

    def repaired(message: Any) -> Any:
        if repair is None:
            return message
        return repair(ChatResult(generations=[ChatGeneration(message=message)]), tools).generations[0].message

    return model.bind(stream=True, stream_usage=True) | RunnableLambda(repaired) | parser
