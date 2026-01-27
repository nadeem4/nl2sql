from __future__ import annotations
from typing import List, Literal, Optional, Dict, Any
from pydantic import BaseModel, Field, model_validator


JsonLiteral = str | int | float | bool | None


class ColumnSpec(BaseModel):
    name: str
    dtype: Optional[str] = None

class RelationSchema(BaseModel):
    columns: List[ColumnSpec]

    @model_validator(mode="after")
    def validate_unique_columns(self):
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"Duplicate columns in schema: {names}")
        return self

class LogicalNode(BaseModel):
    node_id: str
    kind: Literal[
        "scan",
        "combine",
        "post_filter",
        "post_aggregate",
        "post_project",
        "post_sort",
        "post_limit",
    ]
    inputs: List[str] = Field(default_factory=list)
    output_schema: RelationSchema
    attributes: Dict[str, Any] = Field(default_factory=dict)


class LogicalEdge(BaseModel):
    edge_id: str
    from_id: str
    to_id: str
    role: Optional[str] = None


class ExecutionDAG(BaseModel):
    dag_id: Optional[str] = None
    content_hash: Optional[str] = None
    nodes: List[LogicalNode]
    edges: List[LogicalEdge]
    layers: List[List[str]] = Field(default_factory=list)
    version: str = "v1"

    @model_validator(mode="after")
    def populate_layers(self):
        if not self.layers and self.nodes:
            self.layers = self._layered_toposort(self.nodes, self.edges)
        return self

    @staticmethod
    def _layered_toposort(
        nodes: List[LogicalNode],
        edges: List[LogicalEdge],
    ) -> List[List[str]]:
        node_ids = {n.node_id for n in nodes}
        indegree: Dict[str, int] = {n.node_id: 0 for n in nodes}
        dependents: Dict[str, List[str]] = {n.node_id: [] for n in nodes}

        for edge in edges:
            if edge.from_id not in node_ids or edge.to_id not in node_ids:
                continue
            indegree[edge.to_id] += 1
            dependents[edge.from_id].append(edge.to_id)

        layers: List[List[str]] = []
        ready = sorted([n_id for n_id, deg in indegree.items() if deg == 0])
        processed = 0

        while ready:
            current_layer = ready
            layers.append(current_layer)
            processed += len(current_layer)

            next_ready: List[str] = []
            for node_id in current_layer:
                for child in sorted(dependents.get(node_id, [])):
                    indegree[child] -= 1
                    if indegree[child] == 0:
                        next_ready.append(child)
            ready = sorted(set(next_ready))

        if processed != len(nodes):
            raise ValueError("ExecutionDAG contains a cycle; layered topo sort failed.")

        return layers


class GlobalPlannerResponse(BaseModel):
    execution_dag: Optional[ExecutionDAG] = None


