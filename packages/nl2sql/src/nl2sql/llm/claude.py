"""Claude on Anthropic's own Messages API, with the system prompt cached.

Imported only when an agent's provider is ``anthropic``: ``langchain-anthropic``
is the optional ``nl2sql-engine[anthropic]`` extra, and importing this module
without it raises ``ImportError``, which the registry turns into an install hint.
"""
from __future__ import annotations

from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic

# Every breakpoint is the 5-minute ephemeral cache: a question's planner,
# refiner and decomposer calls land well inside it.
CACHE_CONTROL = {"type": "ephemeral"}

# The SDK refuses a non-streaming call whose max_tokens could outlast its
# 10-minute timeout, and every node call here is non-streaming. 16,000 leaves
# room for adaptive thinking before the structured answer.
MAX_TOKENS = 16000


class CachingChatAnthropic(ChatAnthropic):
    """``ChatAnthropic`` that puts a cache breakpoint on the system prompt.

    The planner, refiner and decomposer send a stable system message and a
    variable human message. Anthropic renders tools, then system, then
    messages, so a breakpoint on the last system block caches the tool schema
    and the system prompt, and the question after it varies freely. A prompt
    with no system message is sent unmarked.

    Like ``ConfiguredChatOpenAI``, a model's refusal of ``temperature`` is
    reported with what to set in the config, not retried without it.
    """

    def _get_request_payload(self, input_: Any, *, stop: Any = None, **kwargs: Any) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        system = payload.get("system")
        if system:
            blocks = [{"type": "text", "text": system}] if isinstance(system, str) else list(system)
            blocks[-1] = {**blocks[-1], "cache_control": CACHE_CONTROL}
            payload["system"] = blocks
        return payload

    def _explain(self, exc: anthropic.BadRequestError) -> ValueError:
        from nl2sql.llm.registry import temperature_error

        return temperature_error(self.model, self.tags, exc.message or str(exc))

    def _generate(self, *args: Any, **kwargs: Any):
        try:
            return super()._generate(*args, **kwargs)
        except anthropic.BadRequestError as exc:
            if "temperature" in str(exc):
                raise self._explain(exc) from exc
            raise

    async def _agenerate(self, *args: Any, **kwargs: Any):
        try:
            return await super()._agenerate(*args, **kwargs)
        except anthropic.BadRequestError as exc:
            if "temperature" in str(exc):
                raise self._explain(exc) from exc
            raise


def build_claude_client(model: str, temperature: Any, **kwargs: Any) -> CachingChatAnthropic:
    """A Claude client sending exactly the configured temperature, or none.

    Claude Opus 5 and Sonnet 5 reject any sampling parameter with HTTP 400, so
    ``temperature: null`` in the config is what sends nothing.
    """
    return CachingChatAnthropic(model=model, temperature=temperature, max_tokens=MAX_TOKENS, **kwargs)
