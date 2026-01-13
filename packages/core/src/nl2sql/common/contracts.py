"""
Contract definitions for the Execution Service Interface.

This module defines the Pydantic models used to communicate between the 
Control Plane (Nodes) and the Data Plane (Execution Sandbox).
"""
from typing import Dict, Any, Optional, Literal, List
from pydantic import BaseModel, Field, ConfigDict

class ExecutionRequest(BaseModel):
    """Payload for requesting an operation from the properties sandbox."""
    
    mode: Literal["execute", "dry_run", "cost_estimate", "fetch_schema"] = Field(
        ..., description="The type of operation to perform."
    )
    datasource_id: str = Field(..., description="The unique ID of the target datasource.")
    engine_type: str = Field(..., description="The SQL engine type (e.g. postgres, sqlite).")
    connection_args: Dict[str, Any] = Field(
        ..., description="Connection arguments (host, user, password, etc.)."
    )
    sql: Optional[str] = Field(None, description="The SQL query to execute (required for execute/dry_run).")
    parameters: Dict[str, Any] = Field(
        default_factory=dict, description="Query parameters for parameterized execution."
    )
    limits: Dict[str, int] = Field(
        default_factory=dict, 
        description="Execution limits (e.g., {'row_limit': 1000, 'timeout_ms': 5000})."
    )

    model_config = ConfigDict(extra="ignore")


class ExecutionResult(BaseModel):
    """Standardized response from the execution sandbox."""
    
    success: bool = Field(..., description="Whether the operation completed successfully.")
    data: Optional[Any] = Field(
        None, description="The result payload (Rows, CostEstimate, Schema, etc.)."
    )
    error: Optional[str] = Field(None, description="Error message if failed.")
    metrics: Dict[str, float] = Field(
        default_factory=dict, description="Performance metrics (e.g., execution_time_ms)."
    )

    model_config = ConfigDict(extra="ignore")
