"""Subgraph registry and implementations."""

from .schemas import SubgraphOutput

__all__ = ["SubgraphSpec", "build_subgraph_registry", "SubgraphOutput"]


def __getattr__(name: str):
    if name in {"SubgraphSpec", "build_subgraph_registry"}:
        from .registry import SubgraphSpec, build_subgraph_registry
        return {"SubgraphSpec": SubgraphSpec, "build_subgraph_registry": build_subgraph_registry}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
