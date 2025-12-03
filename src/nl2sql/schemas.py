from __future__ import annotations


from typing import Any, Dict, List, Literal, Optional, TypedDict, Annotated
import operator

from pydantic import BaseModel, ConfigDict, Field


def reduce_latency(left: Dict[str, float], right: Dict[str, float]) -> Dict[str, float]:
    """Reduces latency dictionaries by summing values for the same key."""
    if not left:
        return right
    if not right:
        return left
    new_latency = left.copy()
    for k, v in right.items():
        new_latency[k] = new_latency.get(k, 0.0) + v
    return new_latency


class ColumnRef(BaseModel):
    """
    Represents a reference to a column or an expression in the query plan.

    Attributes:
        expr: The full expression (e.g., "t1.name" or "COUNT(*)").
        alias: The output column alias (e.g., "total_orders"). Only used in select_columns.
        is_derived: True if expr is a derived expression (aggregation, function), False otherwise.
    """
    model_config = ConfigDict(extra="forbid")
    expr: str
    alias: Optional[str] = None
    is_derived: bool = False


class ForeignKey(BaseModel):
    """
    Represents a foreign key relationship.

    Attributes:
        column: The column in the source table.
        referred_table: The table referenced by the foreign key.
        referred_column: The column in the referred table.
    """
    model_config = ConfigDict(extra="forbid")
    column: str
    referred_table: str
    referred_column: str


class TableInfo(BaseModel):
    """
    Represents schema information for a single table.

    Attributes:
        name: The actual name of the table in the database.
        alias: The alias assigned to the table (e.g., "t1").
        columns: List of column names (pre-aliased, e.g., "t1.id").
        foreign_keys: List of foreign keys defined on this table.
    """
    model_config = ConfigDict(extra="forbid")
    name: str
    alias: str
    columns: List[str]
    foreign_keys: List[ForeignKey] = Field(default_factory=list)


class TableRef(BaseModel):
    """
    Represents a table reference in the execution plan.

    Attributes:
        name: The name of the table.
        alias: The alias used for the table in the query.
    """
    model_config = ConfigDict(extra="forbid")
    name: str
    alias: str


class JoinSpec(BaseModel):
    """
    Represents a join operation between two tables.

    Attributes:
        left: The left table name or alias.
        right: The right table name or alias.
        on: List of join conditions (e.g., ["t1.id = t2.user_id"]).
        join_type: The type of join (inner, left, right, full).
    """
    model_config = ConfigDict(extra="forbid")
    left: str
    right: str
    on: List[str]
    join_type: Literal["inner", "left", "right", "full"]


class FilterSpec(BaseModel):
    """
    Represents a filter condition (WHERE clause).

    Attributes:
        column: The column or expression being filtered.
        op: The operator (e.g., "=", ">", "LIKE").
        value: The value to compare against.
        logic: Logical operator to combine with previous filters (and/or).
    """
    model_config = ConfigDict(extra="forbid")
    column: ColumnRef
    op: str
    value: str | int | float | bool
    logic: Optional[Literal["and", "or"]] = None


class AggregateSpec(BaseModel):
    """
    Represents an aggregation function (deprecated, use ColumnRef with is_derived=True).

    Attributes:
        expr: The aggregation expression.
        alias: The output alias.
    """
    model_config = ConfigDict(extra="forbid")
    expr: str
    alias: Optional[str] = None


class HavingSpec(BaseModel):
    """
    Represents a HAVING clause condition.

    Attributes:
        expr: The expression being filtered (usually an aggregation).
        op: The operator.
        value: The value to compare against.
        logic: Logical operator (and/or).
    """
    model_config = ConfigDict(extra="forbid")
    expr: str
    op: str
    value: str | int | float | bool
    logic: Optional[Literal["and", "or"]] = None


class OrderSpec(BaseModel):
    """
    Represents an ORDER BY clause.

    Attributes:
        column: The column to sort by.
        direction: Sort direction ("asc" or "desc").
    """
    model_config = ConfigDict(extra="forbid")
    column: ColumnRef
    direction: Literal["asc", "desc"]


class GeneratedSQL(BaseModel):
    """
    Represents the generated SQL output.

    Attributes:
        sql: The generated SQL query string.
        rationale: Explanation of the generation logic.
        limit_enforced: Whether the row limit was enforced.
        draft_only: Whether this is a draft or final SQL.
    """
    model_config = ConfigDict(extra="ignore")
    sql: str
    rationale: Optional[str] = None
    limit_enforced: bool
    draft_only: bool


class SchemaInfo(BaseModel):
    """
    Represents the full schema information available to the planner.

    Attributes:
        tables: List of table information.
    """
    tables: List[TableInfo] = Field(default_factory=list)


class IntentModel(BaseModel):
    """
    Represents the analyzed intent of the user query.

    Attributes:
        entities: Named entities extracted from the query.
        filters: Filters or constraints extracted from the query.
        keywords: Technical keywords or table names.
        clarifications: Clarifying questions if ambiguous.
        reasoning: Step-by-step reasoning for intent classification.
        query_expansion: Synonyms and related terms.
        query_type: Classification of the query intent (READ, WRITE, DDL).
    """
    model_config = ConfigDict(extra="forbid")
    entities: list[str] = Field(default_factory=list)
    filters: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    clarifications: list[str] = Field(default_factory=list)
    reasoning: Optional[str] = Field(None)
    query_expansion: list[str] = Field(default_factory=list)
    query_type: Literal["READ", "WRITE", "DDL", "UNKNOWN"] = Field("READ")


class PlanModel(BaseModel):
    """
    Represents the structured execution plan for the SQL query.

    Attributes:
        tables: List of tables involved in the query.
        joins: List of join operations.
        filters: List of WHERE clause filters.
        group_by: List of columns to group by.
        having: List of HAVING clause conditions.
        order_by: List of sorting specifications.
        limit: Row limit.
        select_columns: List of columns to select (including aggregations).
        reasoning: Reasoning for the plan choices.
        query_type: The type of query (READ, WRITE, DDL).
    """
    model_config = ConfigDict(extra="forbid")
    tables: list[TableRef] = Field(default_factory=list)
    joins: list[JoinSpec] = Field(default_factory=list)
    filters: list[FilterSpec] = Field(default_factory=list)
    group_by: list[ColumnRef] = Field(default_factory=list)
    having: list[HavingSpec] = Field(default_factory=list)
    order_by: list[OrderSpec] = Field(default_factory=list)
    limit: Optional[int] = None
    select_columns: list[ColumnRef] = Field(default_factory=list)
    reasoning: Optional[str] = Field(None)
    query_type: Literal["READ", "WRITE", "DDL", "UNKNOWN"] = Field("READ")

class ExecutionModel(BaseModel):
    """
    Represents the result of an SQL execution.
    """
    row_count: int = Field(description="Number of rows returned")
    rows: List[Dict[str, Any]] = Field(default_factory=list, description="The actual data rows")
    columns: List[str] = Field(default_factory=list, description="Column names")
    error: Optional[str] = Field(None, description="Error message if execution failed")

    model_config = ConfigDict(extra="allow")


class GraphState(BaseModel):
    """
    Represents the state of the LangGraph execution.

    Attributes:
        user_query: The original user query.
        plan: The generated execution plan (as a dict).
        sql_draft: The generated SQL draft.
        schema_info: The retrieved schema information.
        validation: Validation results and metadata.
        execution: Execution results.
        retrieved_tables: List of tables retrieved from vector store.
        latency: Latency metrics for each step.
        errors: List of errors encountered during execution.
        retry_count: Number of retries attempted.
        thoughts: Chain of thought logs from each node.
        datasource_id: The ID of the selected datasource.
        sub_queries: List of sub-queries for cross-db execution.
        intermediate_results: List of results from sub-queries.
    """
    model_config = ConfigDict(extra="ignore", arbitrary_types_allowed=True)
    
    user_query: str
    plan: Optional[Dict[str, Any]] = None
    sql_draft: Optional[GeneratedSQL] = None
    schema_info: Optional[SchemaInfo] = None
    validation: Dict[str, Any] = Field(default_factory=dict)
    execution: Optional[ExecutionModel] = None
    retrieved_tables: Optional[List[str]] = None
    latency: Annotated[Dict[str, float], reduce_latency] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)
    retry_count: int = 0
    thoughts: Dict[str, List[str]] = Field(default_factory=dict)
    datasource_id: Optional[str] = None
    sub_queries: Optional[List[str]] = None
    intermediate_results: Annotated[List[Any], operator.add] = Field(default_factory=list)
    final_answer: Optional[str] = None


class SQLModel(BaseModel):
    """
    Represents the structured output for SQL generation (used by LLM).

    Attributes:
        sql: The generated SQL.
        rationale: The rationale behind the SQL.
        limit_enforced: Whether limit was enforced.
        draft_only: Whether it is a draft.
    """
    model_config = ConfigDict(extra="forbid")
    sql: str
    rationale: Optional[str] = None
    limit_enforced: Optional[bool] = None
    draft_only: Optional[bool] = None


class DecomposerResponse(BaseModel):
    """Structured response for the query decomposer."""
    sub_queries: List[str] = Field(
        description="List of sub-queries. If no decomposition is needed, this list should contain only the original query."
    )
    reasoning: str = Field(description="Reasoning for why the query was decomposed (or not).")


class AggregatedResponse(BaseModel):
    """Structured response for the aggregator."""
    summary: str = Field(description="A concise summary of the aggregated results.")
    format_type: Literal["table", "list", "text"] = Field(
        description="The best format to present the data: 'table' for structured data, 'list' for items, 'text' for narrative."
    )
    content: str = Field(description="The aggregated content formatted according to format_type (e.g., Markdown table, bullet points, or paragraph).")
