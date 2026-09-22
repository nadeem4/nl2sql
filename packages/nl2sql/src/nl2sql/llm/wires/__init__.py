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

__all__ = ["WIRES", "Wire", "structured", "temperature_error", "wire_named", "wire_of"]


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
