"""What every wire adapter provides, and the helpers they share.

A *wire type* is the HTTP protocol a provider speaks. OpenAI, OpenRouter and
Ollama all speak OpenAI's; Anthropic speaks its own Messages API. Everything
that differs between wire types lives in one adapter per wire type
(``openai.py``, ``anthropic.py``); the registry and the usage callback only
ask the adapter.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import LLMResult


class Wire(Protocol):
    """One wire type.

    Attributes:
        name: The wire type, as a provider preset names it; also the
            ``ls_provider`` LangChain reports for the adapter's client.
        llm_type: The ``_llm_type`` of the client the adapter builds.
        structured_output_method: The ``with_structured_output`` method every
            node uses on this wire.
    """

    name: str
    llm_type: str
    structured_output_method: str

    def build_client(self, model: str, temperature: Optional[float], **kwargs: Any) -> BaseChatModel:
        """A client sending exactly the configured temperature, or none when it is None.

        ``kwargs`` carries ``api_key``, ``tags`` (the agent name first) and,
        when set, ``base_url``.
        """

    def mark_cache(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """The request payload with any prompt-cache breakpoints this wire needs."""

    def read_usage(self, response: LLMResult) -> Optional[Dict[str, int]]:
        """Token counts in the engine's fields, or None if the response reports none."""


def temperature_error(model: str, tags: Optional[list], reason: str) -> ValueError:
    """The error for a model that refused the configured temperature: what to set, and where."""
    agent = tags[0] if tags else "default"
    return ValueError(
        f"Model '{model}' (LLM agent '{agent}') rejected the temperature "
        f"parameter (HTTP 400: {reason}) Set 'temperature: null' for agent '{agent}' "
        "in the LLM config file so no temperature is sent to this model."
    )


def as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def usage_metadata_of(response: LLMResult) -> Optional[Mapping[str, Any]]:
    """The first ``AIMessage.usage_metadata`` in a chat result, if any."""
    for generations in response.generations or []:
        for generation in generations:
            usage = getattr(getattr(generation, "message", None), "usage_metadata", None)
            if usage:
                return usage
    return None


def usage(input_tokens: int, cached: int, cache_write: int, output: int, reasoning: int,
          total: int = 0) -> Dict[str, int]:
    """The engine's usage fields. Cached and cache-write are subsets of input."""
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_write_input_tokens": cache_write,
        "output_tokens": output,
        "reasoning_tokens": reasoning,
        "total_tokens": total or input_tokens + output,
    }
