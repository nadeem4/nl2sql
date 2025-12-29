from typing import Any, List
from sqlalchemy import create_engine, text, inspect
from nl2sql_adapter_sdk import (
    DatasourceAdapter, 
    CapabilitySet, 
    SchemaMetadata, 
    Table, 
    Column, 
    QueryResult, 
    CostEstimate,
    DryRunResult,
    QueryPlan,
    ExecutionMetrics
)

class MssqlAdapter(DatasourceAdapter):
    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        # Use pyodbc if simple string, or assume SQLAlchemy URL
        self.engine = create_engine(connection_string)


    def connect(self, config: dict) -> None:
        pass

    def validate_connection(self) -> bool:
        try:
             with self.engine.connect() as conn:
                 conn.execute(text("SELECT 1"))
             return True
        except Exception:
             return False

    def dry_run(self, query: str) -> DryRunResult:
        # MSSQL SET NOEXEC ON is tricky with SQLAlchemy connection pooling
        # usage simple parse-check if possible or assumption
        return DryRunResult(valid=True, error=None)

    def explain(self, query: str) -> QueryPlan:
         # SHOWPLAN_XML
         return QueryPlan(original_query=query, plan="Not implemented")

    def metrics(self) -> ExecutionMetrics:
        return ExecutionMetrics(execution_time_ms=0.0, rows_returned=0)

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            supports_cte=True,
            supports_window_functions=True,
            supports_limit_offset=False,
            supports_multi_db_join=False,
            supports_dry_run=False
        )



    def fetch_schema(self) -> SchemaMetadata:
        inspector = inspect(self.engine)
        tables = []
        table_names = [t for t in inspector.get_table_names() if not t.startswith("#")]
        
        for t_name in table_names:
            columns = []
            for col in inspector.get_columns(t_name):
                columns.append(Column(
                    name=col["name"],
                    type=str(col["type"]),
                    is_nullable=col.get("nullable", True),
                    is_primary_key=col.get("primary_key", False)
                ))
            tables.append(Table(name=t_name, columns=columns))
            
        return SchemaMetadata(
            datasource_id="mssql_ds", # Should probably be passed in init?
            tables=tables
        )

    def execute(self, query: str) -> QueryResult:
        with self.engine.connect() as conn:
            result = conn.execute(text(query))
            if result.returns_rows:
                rows = result.fetchall()
                # rows are tuples/Row objects. Convert to list of list explicitly?
                # SDK expects List[List[Any]]
                data = [list(row) for row in rows]
                cols = list(result.keys())
                return QueryResult(
                    columns=cols,
                    rows=data,
                    row_count=len(data)
                )
            else:
                 # Update/Insert/etc
                 return QueryResult(
                     columns=[], 
                     rows=[], 
                     row_count=result.rowcount
                 )

    def cost_estimate(self, query: str) -> CostEstimate:
        # Basic safeguard for now
        return CostEstimate(estimated_cost=10.0, estimated_rows=100) # Stub
