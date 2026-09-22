"""The Anthropic wire: Claude on Anthropic's own Messages API.

Importable without ``langchain-anthropic``: the client module, which needs it,
is imported only when a client is built.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.outputs import LLMResult

from .base import as_int, usage, usage_metadata_of

EXTRA_HINT = 'Install the anthropic extra: pip install "nl2sql-engine[anthropic]"'

# Every breakpoint is the 5-minute ephemeral cache: a question's LLM calls land
# well inside it.
CACHE_CONTROL = {"type": "ephemeral"}

# When Anthropic reports a cache write per TTL, langchain-anthropic zeroes the
# generic ``cache_creation`` detail and puts the tokens under these keys.
_CACHE_WRITE_BY_TTL = ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")


class AnthropicWire:
    """Anthropic's Messages API, through langchain-anthropic's ``ChatAnthropic``.

    Structured output is a forced tool call (``function_calling``): Anthropic's
    native JSON outputs reject recursive schemas, and ``PlanModel`` is one.
    Prompt caching is explicit: the last system block gets a breakpoint.
    """

    name = "anthropic"
    llm_type = "anthropic-chat"
    structured_output_method = "function_calling"

    def build_client(self, model: str, temperature: Optional[float], **kwargs: Any) -> BaseChatModel:
        try:
            from .anthropic_client import build_claude_client
        except ImportError as exc:
            agent = (kwargs.get("tags") or ["default"])[0]
            raise ValueError(
                f"LLM agent '{agent}' uses the anthropic wire, which needs langchain-anthropic. {EXTRA_HINT}"
            ) from exc
        return build_claude_client(model, temperature, **kwargs)

    def mark_cache(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Puts a breakpoint on the last system block.

        Anthropic renders tools, then system, then messages, so this caches the
        tool schema and the system prompt, and the question after it varies
        freely. A payload with no system prompt is left unmarked.
        """
        system = payload.get("system")
        if system:
            blocks = [{"type": "text", "text": system}] if isinstance(system, str) else list(system)
            blocks[-1] = {**blocks[-1], "cache_control": CACHE_CONTROL}
            payload["system"] = blocks
        return payload

    def read_usage(self, response: LLMResult) -> Optional[Dict[str, int]]:
        """Anthropic's usage in the engine's fields, each input token counted once.

        Anthropic's own ``input_tokens`` excludes cache reads and writes;
        langchain-anthropic adds both back, so ``input_tokens`` here is the
        whole prompt and both cache fields are subsets of it.
        """
        metadata = usage_metadata_of(response)
        if not metadata:
            return None
        details = metadata.get("input_token_details") or {}
        return usage(
            as_int(metadata.get("input_tokens")),
            as_int(details.get("cache_read")),
            as_int(details.get("cache_creation")) + sum(as_int(details.get(k)) for k in _CACHE_WRITE_BY_TTL),
            as_int(metadata.get("output_tokens")),
            as_int((metadata.get("output_token_details") or {}).get("reasoning")),
            as_int(metadata.get("total_tokens")),
        )
