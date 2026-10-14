"""Stand-ins for the facade's namespaces, for tests of a caller (the playground) over a fake runtime."""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Union

from nl2sql.api.datasource_api import DatasourcesAPI
from nl2sql.api.types import DatasourceInfo


class StubDatasources(DatasourcesAPI):
    """``engine.datasources`` listing ``ids`` as ready; ``schema`` reads the runtime's state store."""

    def __init__(self, ctx: Any, ids: Union[Iterable[str], Callable[[], Iterable[str]]]):
        super().__init__(ctx)
        self._ids = ids

    def list(self):
        ids = self._ids() if callable(self._ids) else self._ids
        return [DatasourceInfo(id=i, type="stub", description=None, status="ready") for i in ids]


class StubLLMs:
    """``engine.llm`` answering ``list`` and ``get`` from ``listing``: agent name -> entry, ``default`` included.

    ``providers`` answers as a real engine on the default registry does.
    """

    def __init__(self, listing: Union[Dict[str, Dict[str, Any]], Callable[[], Dict[str, Dict[str, Any]]]]):
        self._listing = listing

    def list(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._listing() if callable(self._listing) else self._listing)

    def get(self, agent: str) -> Dict[str, Any]:
        listed = self.list()
        if agent not in listed:
            raise ValueError(f"Unknown agent '{agent}'.")
        return listed[agent]

    def providers(self) -> List[Dict[str, Any]]:
        from nl2sql.llm.providers import describe_providers
        from nl2sql.registry import Registry

        return describe_providers(Registry.default())
