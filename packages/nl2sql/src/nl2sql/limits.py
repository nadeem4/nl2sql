"""Limits on model calls and dollars: ``Budget``, and ``RunStop``, why it stopped the work.

docs/architecture/wave_a.md, section 5, "Per-question caps" (D20). A question
runs under a ``Budget`` built from ``settings.pipeline.max_llm_calls`` (60)
and ``max_cost_usd`` (none), which ``QueryAPI.open_scope`` attaches to the
run's scope. Work that is not a question (Wave B's profiler drafting, V3's
seeding) opens a ``Budget`` of its own, with its own caps, and makes its model
calls with ``budget.callbacks()``; the semantics are the same.

**Checked before each call.** The budget is a LangChain callback: on
``on_chat_model_start`` (and ``on_llm_start``) it refuses the call when the
calls already started have reached ``max_llm_calls``, or the dollars already
spent (``TokenUsageCallback.spent()``) have reached ``max_cost_usd``. A
refused call is never sent. The calls in flight finish, so the cost can pass
the cap by at most the calls in flight: one per running sub-query.

**Stopping is through the token, not only the raise.** On the first refusal
the budget records ``stop`` (a ``RunStop``), cancels every token bound to it
with that reason, publishes ``run.limit_reached`` on the current run's scope
(nothing outside one), and raises ``LimitReached``. The nodes wrap their model
calls in ``except Exception`` and would retry the raise as a planner error; the
cancelled token is what ends the run (the SQL agent's routers read it, and
``run_with_graph``'s ``_StopWhenCancelled`` refuses every later step), and
``run_with_graph`` maps a token cancelled with a ``RunStop`` to
``status: "stopped"``. Every later call is refused with the same stop.
"""
from __future__ import annotations

import threading
from typing import Any, List, Literal, Optional

from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel

from nl2sql.common.cancellation import CancellationToken

Limit = Literal["max_cost_usd", "max_llm_calls"]

# Where a question's caps are set; the stop's message names the setting.
QUESTION_SETTING = "settings.pipeline"


class RunStop(BaseModel):
    """A run stopped at a limit: which one, its configured ``value``, and what was ``spent`` when it stopped."""

    limit: Limit
    value: float
    spent: float


def describe(stop: RunStop, setting: str = QUESTION_SETTING) -> str:
    """``limit of 60 model calls (settings.pipeline.max_llm_calls)``: the limit, and where it is set."""
    if stop.limit == "max_llm_calls":
        calls = int(stop.value)
        return f"limit of {calls} model call{'' if calls == 1 else 's'} ({setting}.max_llm_calls)"
    return f"cost limit of ${stop.value:.2f} ({setting}.max_cost_usd)"


def stop_answer(stop: RunStop, setting: str = QUESTION_SETTING) -> str:
    """What a stopped question answers: "This question reached its limit of 60 model calls (...)."."""
    return f"This question reached its {describe(stop, setting)}."


class LimitReached(Exception):
    """A model call a ``Budget`` refused; ``stop`` says which limit."""

    def __init__(self, stop: RunStop, setting: str = QUESTION_SETTING) -> None:
        super().__init__(f"Reached the {describe(stop, setting)}; no more model calls are made.")
        self.stop = stop


class Budget(BaseCallbackHandler):
    """Caps on model calls and dollars, checked before each call.

    ``usage`` is the usage collector whose ``spent()`` the cost cap compares
    (a question's is its scope's; a new one when not given), ``token`` the
    budget's own ``CancellationToken`` (a new one when not given), and
    ``setting`` where the caps are configured, for the messages
    (``settings.pipeline`` for a question). A cap of None is no cap.
    """

    raise_error = True

    def __init__(self, *, max_llm_calls: Optional[int] = None, max_cost_usd: Optional[float] = None,
                 usage: Any = None, token: Optional[CancellationToken] = None,
                 setting: str = QUESTION_SETTING) -> None:
        from nl2sql.services.callbacks.token_handler import TokenUsageCallback

        self.max_llm_calls = max_llm_calls
        self.max_cost_usd = max_cost_usd
        self.usage = usage if usage is not None else TokenUsageCallback()
        self.token = token or CancellationToken()
        self.setting = setting
        self.stop: Optional[RunStop] = None
        self._tokens: List[CancellationToken] = [self.token]
        self._started = 0
        self._lock = threading.Lock()

    @classmethod
    def for_question(cls, pipeline: Any, usage: Any) -> Optional["Budget"]:
        """The budget of a question under ``pipeline`` (``settings.pipeline``); None when both caps are off."""
        if pipeline.max_llm_calls is None and pipeline.max_cost_usd is None:
            return None
        return cls(max_llm_calls=pipeline.max_llm_calls, max_cost_usd=pipeline.max_cost_usd, usage=usage)

    @property
    def calls(self) -> int:
        """The model calls this budget let start."""
        return self._started

    def callbacks(self) -> List[Any]:
        """The callbacks a model call under this budget is made with: the budget, then its usage collector.

        The budget comes first, so a call it refuses is never opened in the usage.
        """
        return [self, self.usage]

    def bind(self, token: CancellationToken) -> None:
        """Cancel ``token`` too when the budget stops (``run_with_graph`` binds the run's token)."""
        with self._lock:
            self._tokens.append(token)
            stop = self.stop
        if stop is not None:
            token.cancel(reason=stop)

    def _check(self, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            first = self.stop is None
            if first:
                spent = self.usage.spent() if self.max_cost_usd is not None else 0.0
                if self.max_llm_calls is not None and self._started >= self.max_llm_calls:
                    self.stop = RunStop(limit="max_llm_calls", value=self.max_llm_calls, spent=self._started)
                elif self.max_cost_usd is not None and spent >= self.max_cost_usd:
                    self.stop = RunStop(limit="max_cost_usd", value=self.max_cost_usd, spent=spent)
                else:
                    self._started += 1
                    return
            stop, tokens = self.stop, list(self._tokens)
        if first:
            from nl2sql.events import publish

            for token in tokens:
                token.cancel(reason=stop)
            publish("run.limit_reached", stop)
        raise LimitReached(stop, self.setting)

    on_chat_model_start = _check
    on_llm_start = _check
