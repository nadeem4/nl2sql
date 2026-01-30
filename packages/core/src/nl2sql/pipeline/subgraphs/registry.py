from dataclasses import dataclass
from typing import Any, Callable, Dict, Set

from nl2sql_adapter_sdk.capabilities import DatasourceCapability
from nl2sql.context import NL2SQLContext
from nl2sql.pipeline.subgraphs.sql_agent import build_sql_agent_graph


@dataclass(frozen=True)
class SubgraphSpec:
    name: str
    required_capabilities: Set[str]
    builder: Callable[[NL2SQLContext], Any]


def build_subgraph_registry(ctx: NL2SQLContext) -> Dict[str, SubgraphSpec]:
    """Registers all available subgraphs for routing."""
    return {
            "sql_agent": SubgraphSpec(
                name="sql_agent",
                required_capabilities={DatasourceCapability.SUPPORTS_SQL.value},
                builder=build_sql_agent_graph,
            ),
    }
