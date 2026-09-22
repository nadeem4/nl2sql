"""The OpenAI wire: OpenAI, OpenRouter, Ollama and any OpenAI-compatible endpoint."""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import openai
from langchain_core.outputs import LLMResult
from langchain_openai import ChatOpenAI

from .base import as_int, temperature_error, usage, usage_metadata_of

SEED = 42


def _rejects_temperature(exc: openai.BadRequestError) -> bool:
    return getattr(exc, "param", None) == "temperature" or "'temperature'" in str(exc)


class ConfiguredChatOpenAI(ChatOpenAI):
    """``ChatOpenAI`` that explains a model's refusal of the temperature parameter.

    Some models accept only their default temperature and answer anything else
    with HTTP 400 (gpt-5.5 and gpt-5-mini did on 2026-09-20). The fix is in the
    config, so the error says which model and which agent, and what to set.
    Nothing is retried without the parameter: the config says what is sent.
    """

    def _explain(self, exc: openai.BadRequestError) -> ValueError:
        body = exc.body if isinstance(exc.body, dict) else {}
        return temperature_error(self.model_name, self.tags, body.get("message") or str(exc))

    def _generate(self, *args: Any, **kwargs: Any):
        try:
            return super()._generate(*args, **kwargs)
        except openai.BadRequestError as exc:
            if _rejects_temperature(exc):
                raise self._explain(exc) from exc
            raise

    async def _agenerate(self, *args: Any, **kwargs: Any):
        try:
            return await super()._agenerate(*args, **kwargs)
        except openai.BadRequestError as exc:
            if _rejects_temperature(exc):
                raise self._explain(exc) from exc
            raise


def build_chat_client(model: str, temperature: Optional[float], **kwargs: Any) -> ConfiguredChatOpenAI:
    """Builds a client that sends exactly the configured temperature, or none.

    langchain-openai silently drops any temperature other than 1 for a model
    whose name starts with ``gpt-5`` (its validator runs before construction).
    gpt-5.4 accepts 0, so the temperature is set after construction and the
    config, not the model name, decides what goes on the wire. ``None`` sends
    no temperature at all.
    """
    client = ConfiguredChatOpenAI(model=model, seed=SEED, **kwargs)
    client.temperature = temperature
    return client


def _detail(details: Mapping[str, Any], key: str) -> int:
    """Read a usage detail, including its service-tier-prefixed variants.

    langchain-openai writes ``priority_cache_read`` rather than ``cache_read``
    for priority/flex tier calls.
    """
    return sum(as_int(v) for k, v in (details or {}).items() if k == key or k.endswith(f"_{key}"))


class OpenAIWire:
    """OpenAI's chat completions protocol.

    Structured output is a tool call (``function_calling``): it is the method
    OpenRouter's and Ollama's models support most widely. Prompt caching needs
    no marking: OpenAI caches long prefixes by itself, and reports reads as
    ``prompt_tokens_details.cached_tokens``. It reports no cache writes.
    """

    name = "openai"
    llm_type = "openai-chat"
    structured_output_method = "function_calling"

    def build_client(self, model: str, temperature: Optional[float], **kwargs: Any) -> ConfiguredChatOpenAI:
        return build_chat_client(model, temperature, **kwargs)

    def mark_cache(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return payload

    def read_usage(self, response: LLMResult) -> Optional[Dict[str, int]]:
        metadata = usage_metadata_of(response)
        if metadata:
            inp = as_int(metadata.get("input_tokens"))
            out = as_int(metadata.get("output_tokens"))
            return usage(
                inp,
                _detail(metadata.get("input_token_details") or {}, "cache_read"),
                _detail(metadata.get("input_token_details") or {}, "cache_creation"),
                out,
                _detail(metadata.get("output_token_details") or {}, "reasoning"),
                as_int(metadata.get("total_tokens")),
            )

        # A plain (non-chat) LLM only reports the provider's raw usage object.
        output = response.llm_output or {}
        legacy = output.get("token_usage") or output.get("usage")
        if legacy:
            return usage(
                as_int(legacy.get("prompt_tokens") or legacy.get("input_tokens")),
                as_int((legacy.get("prompt_tokens_details") or {}).get("cached_tokens")),
                0,
                as_int(legacy.get("completion_tokens") or legacy.get("output_tokens")),
                as_int((legacy.get("completion_tokens_details") or {}).get("reasoning_tokens")),
                as_int(legacy.get("total_tokens")),
            )
        return None
