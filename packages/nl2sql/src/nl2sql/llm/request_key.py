"""What one request brought with it: its API keys, and its model per step.

A public demo cannot hold a key of its own: every visitor brings theirs, it is
used for their question and then it is gone. :func:`use_request_llms` binds
that to the work of one request and
:class:`~nl2sql.llm.registry.LLMRegistry` builds each step's client from it,
caching nothing, so no second request can be handed a client built with
someone else's key.

A visitor may bring more than one key -- one per provider -- and may put each
step of the pipeline on a provider and model of their own
(:class:`RequestLLMs`). The registry then resolves a step like this:

1. the step's own choice, if the visitor made one: that provider's key is
   required, and :exc:`MissingProviderKey` says which step and which provider
   when it is missing;
2. otherwise the key the visitor sent without naming a provider, which keeps
   the one-key case exactly what it was;
3. otherwise the configured provider's key, if they sent one;
4. otherwise the first key they sent, moved onto its own provider, so a
   visitor who brought no key for the configured provider is never stuck;
5. otherwise -- they sent no key at all -- :exc:`MissingProviderKey`.

A step is therefore refused for a missing key only when the caller chose that
provider for it, which is the one case where naming the step and the provider
says what to fix.

A :class:`~contextvars.ContextVar` is the store because the engine runs one
question per thread: Starlette dispatches the playground's synchronous
``/api/ask`` into its threadpool, and the pipeline's LLM clients are all built
while the graph is constructed, in that same thread. Nothing here is global,
so two visitors asking at once never see each other's keys.

:func:`current_api_keys` is also read by the trace redactor
(:func:`nl2sql.tracing.trace.collect_secrets`), so every key that reached a
prompt or an error is masked in the trace like any other credential.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Dict, Iterator, Mapping, NamedTuple, Optional, Tuple

__all__ = ["MissingProviderKey", "RequestLLMs", "current_api_key", "current_api_keys",
           "current_request_llms", "use_api_key", "use_request_llms"]


class MissingProviderKey(Exception):
    """A step is on a provider this request brought no key for.

    It names the step and the provider and quotes no key, so the playground can
    turn it into a refusal a visitor can act on.
    """

    def __init__(self, agent: str, provider: str) -> None:
        self.agent = agent
        self.provider = provider
        super().__init__(f"No API key was supplied for provider '{provider}', "
                         f"which step '{agent}' is set to run on.")


class RequestLLMs(NamedTuple):
    """The keys and the per-step model choices of one request.

    Attributes:
        keys: Provider name -> the key for it. One entry per provider the
            visitor supplied a key for.
        models: Agent name -> ``(provider, model)``, for the steps the visitor
            chose a model for. A step with no entry runs on the configured one.
        fallback: The key that was sent without naming a provider, if any. It
            is what the one-key case has always used.
    """

    keys: Mapping[str, str] = {}
    models: Mapping[str, Tuple[str, str]] = {}
    fallback: Optional[str] = None

    @classmethod
    def from_key(cls, key: Optional[str]) -> "RequestLLMs":
        """One key, no per-step choices: what a request used to carry."""
        from .providers import provider_for_key

        clean = (key or "").strip()
        if not clean:
            return cls()
        return cls(keys={provider_for_key(clean): clean}, models={}, fallback=clean)

    @property
    def empty(self) -> bool:
        return not self.keys and not self.models

    def key_for(self, provider: str) -> Optional[str]:
        return self.keys.get(provider)

    def all_keys(self) -> Tuple[str, ...]:
        """Every key this request brought, for the trace redactor."""
        found: Dict[str, None] = {}
        for key in self.keys.values():
            found[key] = None
        if self.fallback:
            found[self.fallback] = None
        return tuple(found)

    def resolve(self, agent: str, provider: str) -> Tuple[Optional[str], Optional[str], str]:
        """How ``agent`` should run, given the provider it is configured on.

        Returns:
            ``(provider, model, key)``: the provider and model to move the
            agent onto (each None to leave the configured one alone), and the
            key to build its client with.

        Raises:
            MissingProviderKey: when no key this request brought can serve it.
        """
        choice = self.models.get(agent)
        if choice:
            chosen_provider, chosen_model = choice
            key = self.keys.get(chosen_provider)
            if not key:
                raise MissingProviderKey(agent, chosen_provider)
            return chosen_provider, chosen_model, key
        if self.fallback:
            # One key and no choices: the key names its own provider, exactly
            # as it always has.
            return None, None, self.fallback
        own = self.keys.get(provider)
        if own:
            return None, None, own
        if self.keys:
            # No key for the provider this step is configured on, and the
            # caller chose nothing for it: their first key answers, on its own
            # provider, exactly as a single key always has.
            return None, None, next(iter(self.keys.values()))
        raise MissingProviderKey(agent, provider)


_request_llms: ContextVar[Optional[RequestLLMs]] = ContextVar("nl2sql_request_llms", default=None)


@contextmanager
def use_request_llms(llms: Optional[RequestLLMs]) -> Iterator[None]:
    """Binds ``llms`` to the current context for the duration of the block."""
    bound = llms if (llms and not llms.empty) else None
    token = _request_llms.set(bound)
    try:
        yield
    finally:
        _request_llms.reset(token)


@contextmanager
def use_api_key(key: Optional[str]) -> Iterator[None]:
    """Binds one key, with no per-step choices, for the duration of the block."""
    with use_request_llms(RequestLLMs.from_key(key)):
        yield


def current_request_llms() -> Optional[RequestLLMs]:
    """What this context's request brought, or None when it brought nothing."""
    return _request_llms.get()


def current_api_key() -> Optional[str]:
    """The key sent without naming a provider, or None."""
    bound = _request_llms.get()
    return bound.fallback if bound else None


def current_api_keys() -> Tuple[str, ...]:
    """Every key bound to this context, for the trace redactor."""
    bound = _request_llms.get()
    return bound.all_keys() if bound else ()
