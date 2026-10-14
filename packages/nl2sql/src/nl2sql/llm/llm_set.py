"""``LLMSet``: one engine's LLM, as each agent's settings and client, never changed once built.

A run reads the engine's set once, when it starts, so a run never mixes two
LLMs: ``engine.llm.replace`` builds a new set and swaps the reference, and
runs already in flight finish on the old one. See
docs/architecture/configuration_v2.md, "Replacing the LLM at runtime".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional

from .client_cache import ClientKey
from .models import AgentConfig


def _frozen(mapping: Optional[Mapping]) -> Mapping:
    return MappingProxyType(dict(mapping or {}))


class LLMSet:
    """Each agent's settings and client, keyed as the nodes ask ``get_llm`` (``astplanner``, ...).

    Attributes:
        configs: Agent name -> its settings, the API key as written (a
            reference, never resolved here).
        objects: Agent name -> the chat model object given in place of a
            provider and model.
        clients: Agent name -> its client, for each agent in ``configs`` whose
            client could be built.
        keys: Agent name -> the ``LLMClientCache`` key its client is held under.
        errors: Agent name -> why its client could not be built; ``get_llm``
            raises it.
        missing: Agent name -> the message for an API key that does not resolve.
        llm: The ``llm`` section the set was built from, when it was.
    """

    def __init__(self, *, configs: Optional[Mapping[str, AgentConfig]] = None,
                 objects: Optional[Mapping[str, Any]] = None, clients: Optional[Mapping[str, Any]] = None,
                 keys: Optional[Mapping[str, ClientKey]] = None, errors: Optional[Mapping[str, Exception]] = None,
                 missing: Optional[Mapping[str, str]] = None, llm: Any = None):
        self.configs = _frozen(configs)
        self.objects = _frozen(objects)
        self.clients = _frozen(clients)
        self.keys = _frozen(keys)
        self.errors = _frozen(errors)
        self.missing = _frozen(missing)
        self.llm = llm

    def with_clients(self, clients: Mapping[str, Any]) -> "LLMSet":
        """The same settings, every agent in ``configs`` on the given client. Held by no cache."""
        return LLMSet(configs=self.configs, objects=self.objects, clients=clients, llm=self.llm)

    def config_for(self, name: str) -> AgentConfig:
        """The settings of agent ``name``, or ``default``'s when it has none of its own."""
        config = self.configs.get(name) or self.configs.get("default")
        if config is None:
            raise ValueError(
                f"No LLM named '{name}' is configured and no 'default' LLM has "
                "been registered. Add it to the llm section (under 'agents', or "
                "as the default)."
            )
        return config

    def object_for(self, name: str) -> Optional[Any]:
        """The chat model object agent ``name`` runs on, or None when it runs on settings."""
        if name in self.objects:
            return self.objects[name]
        if name not in self.configs and "default" in self.objects:
            return self.objects["default"]
        return None

    def client(self, name: str) -> Any:
        """Agent ``name``'s client (``default``'s when it has no settings of its own).

        Raises:
            ValueError: no agent of that name and no default, or its API key
                did not resolve.
        """
        model = self.object_for(name)
        if model is not None:
            return model
        self.config_for(name)
        agent = name if name in self.configs else "default"
        if agent in self.errors:
            raise self.errors[agent]
        return self.clients[agent]

    def describe(self, name: str) -> str:
        """``provider/model`` for agent ``name``: what a log line or a cache key names it by."""
        model = self.object_for(name)
        if model is not None:
            return describe_object(model)
        config = self.config_for(name)
        return f"{config.provider}/{config.model}"


def describe_object(model: Any) -> str:
    """``Class/model`` for a chat model object given in place of a provider and model."""
    name = getattr(model, "model_name", None) or getattr(model, "model", None)
    return f"{type(model).__name__}/{name}" if isinstance(name, str) and name else type(model).__name__


@dataclass(frozen=True)
class LLMChange:
    """What ``engine.llm.replace`` changed. It never holds a key, a key reference or a header.

    Attributes:
        by: Who replaced it: ``via`` (the entry point: ``python``, ``api``,
            ``playground``), and ``user_id`` and ``roles`` when known.
        changes: Each agent whose effective provider or model changed, as
            ``{"old": "provider/model", "new": "provider/model"}``.
    """

    by: Dict[str, Any] = field(default_factory=dict)
    changes: Dict[str, Dict[str, str]] = field(default_factory=dict)
