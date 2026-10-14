"""Helpers for tests of the engine and of code built on it."""
from typing import Any, Mapping

from nl2sql.registry import Registry


def adapter_registry(adapters: Mapping[str, Any]) -> Registry:
    """A registry holding exactly these adapters, by name, and nothing else."""
    registry = Registry.empty()
    for name, cls in adapters.items():
        registry.add_adapter(name, cls)
    return registry
