from __future__ import annotations

from pydantic import BaseModel

# The DAG models live in nl2sql.execution.dag, shared with the aggregation service.
from nl2sql.execution.dag import (  # noqa: F401
    ColumnSpec,
    ExecutionDAG,
    JsonLiteral,
    LogicalEdge,
    LogicalNode,
    RelationSchema,
)


class GlobalPlannerResponse(BaseModel):
    execution_dag: ExecutionDAG
