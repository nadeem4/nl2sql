from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class AdapterRequest(BaseModel):
    """Generic adapter request for subgraph execution."""

    plan_type: str = Field(
        ..., description="Execution plan type (e.g., 'sql', 'rest', 'nosql')."
    )
    payload: Dict[str, Any] = Field(
        default_factory=dict, description="Plan-specific request payload."
    )
    parameters: Dict[str, Any] = Field(
        default_factory=dict, description="Optional parameter bindings."
    )
    limits: Dict[str, int] = Field(
        default_factory=dict,
        description="Execution limits (row_limit, timeout_ms, max_bytes).",
    )
    trace_id: Optional[str] = Field(
        default=None, description="Optional trace id for observability."
    )

    model_config = ConfigDict(extra="ignore")


class ResultColumn(BaseModel):
    """Column metadata for a ResultFrame."""

    name: str
    type: str = Field(default="unknown", description="Logical or native column type.")


class ResultError(BaseModel):
    """Standardized error envelope for adapter results."""

    error_code: str
    safe_message: str
    severity: str = Field(default="ERROR")
    retryable: bool = Field(default=False)
    stage: Optional[str] = None
    datasource_id: Optional[str] = None
    error_id: Optional[str] = None


class ResultFrame(BaseModel):
    """Adapter-agnostic, DataFrame-like result contract."""

    success: bool = Field(default=True)
    columns: List[ResultColumn] = Field(default_factory=list)
    rows: List[List[Any]] = Field(default_factory=list)
    row_count: int = Field(default=0)
    truncated: bool = Field(default=False)
    bytes: Optional[int] = Field(default=None)
    datasource_id: Optional[str] = Field(default=None)
    tenant_id: Optional[str] = Field(default=None)
    execution_stats: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[ResultError] = Field(default=None)

    model_config = ConfigDict(extra="ignore")

    @classmethod
    def from_row_dicts(
        cls,
        rows: List[Dict[str, Any]],
        columns: Optional[List[str]] = None,
        *,
        row_count: Optional[int] = None,
        **kwargs: Any,
    ) -> "ResultFrame":
        """Create a ResultFrame from list-of-dict rows."""

        if columns is None:
            columns = list(rows[0].keys()) if rows else []

        col_specs = [ResultColumn(name=col, type="unknown") for col in columns]
        row_values = [[row.get(col) for col in columns] for row in rows]

        return cls(
            columns=col_specs,
            rows=row_values,
            row_count=row_count if row_count is not None else len(row_values),
            **kwargs,
        )

    def to_row_dicts(self) -> List[Dict[str, Any]]:
        """Convert row values to list-of-dict rows using column order."""

        if not self.rows or not self.columns:
            return []
        names = [col.name for col in self.columns]
        return [dict(zip(names, row)) for row in self.rows]
