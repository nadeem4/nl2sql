"""Helpers for tests of the engine and of code built on it."""
from typing import Any, Mapping

from pydantic import ConfigDict

from nl2sql_sdk import ConnectionModel

from nl2sql.registry import Registry


class OpenConnection(ConnectionModel):
    """A connection model that takes any key, for a stub adapter in a test.

    A real adapter declares every field and forbids the rest.
    """

    model_config = ConfigDict(extra="allow")


def adapter_registry(adapters: Mapping[str, Any]) -> Registry:
    """A registry holding exactly these adapters, by name, and nothing else."""
    registry = Registry.empty()
    for name, cls in adapters.items():
        registry.add_adapter(name, cls)
    return registry
