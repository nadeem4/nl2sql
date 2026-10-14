"""The Claude client the anthropic wire builds.

Needs the optional ``nl2sql-engine[anthropic]`` extra: importing this module
without ``langchain-anthropic`` raises ``ImportError``, which
:class:`~nl2sql.llm.wires.anthropic.AnthropicWire` turns into an install hint.
"""
from __future__ import annotations

from typing import Any

import anthropic
from langchain_anthropic import ChatAnthropic

from .anthropic import AnthropicWire
from .base import temperature_error

# The default when the agent sets no ``max_tokens``. The SDK refuses a
# non-streaming call whose max_tokens could outlast its 10-minute timeout, and
# every node call here but the streamed answer of ``stream_query`` is
# non-streaming. 16,000 leaves room for adaptive thinking before the
# structured answer.
MAX_TOKENS = 16000

_WIRE = AnthropicWire()


class CachingChatAnthropic(ChatAnthropic):
    """``ChatAnthropic`` whose requests carry the wire's cache breakpoints.

    Like ``ConfiguredChatOpenAI``, a model's refusal of ``temperature`` is
    reported with what to set in the config, not retried without it.
    """

    def _get_request_payload(self, input_: Any, *, stop: Any = None, **kwargs: Any) -> dict:
        return _WIRE.mark_cache(super()._get_request_payload(input_, stop=stop, **kwargs))

    def _explain(self, exc: anthropic.BadRequestError) -> ValueError:
        return temperature_error(self.model, self.tags, exc.message or str(exc))

    def _generate(self, *args: Any, **kwargs: Any):
        try:
            result = super()._generate(*args, **kwargs)
        except anthropic.BadRequestError as exc:
            if "temperature" in str(exc):
                raise self._explain(exc) from exc
            raise
        return _WIRE.repair_tool_calls(result, kwargs.get("tools"))

    def _stream(self, *args: Any, **kwargs: Any):
        # The streamed answer (``streaming_structured``) skips ``_generate``,
        # and repairs its tool calls itself.
        try:
            yield from super()._stream(*args, **kwargs)
        except anthropic.BadRequestError as exc:
            if "temperature" in str(exc):
                raise self._explain(exc) from exc
            raise

    async def _agenerate(self, *args: Any, **kwargs: Any):
        try:
            result = await super()._agenerate(*args, **kwargs)
        except anthropic.BadRequestError as exc:
            if "temperature" in str(exc):
                raise self._explain(exc) from exc
            raise
        return _WIRE.repair_tool_calls(result, kwargs.get("tools"))


def build_claude_client(model: str, temperature: Any, **kwargs: Any) -> CachingChatAnthropic:
    """A Claude client sending exactly the configured temperature, or none.

    Claude Opus 5 and Sonnet 5 reject any sampling parameter with HTTP 400, so
    ``temperature: null`` in the config is what sends nothing. ``max_tokens``
    is the agent's, else ``MAX_TOKENS``.
    """
    kwargs.setdefault("max_tokens", MAX_TOKENS)
    return CachingChatAnthropic(model=model, temperature=temperature, **kwargs)
