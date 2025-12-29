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

class MysqlAdapter(DatasourceAdapter):
    def __init__(self, connection_string: str):
        self.connection_string = connection_string
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
        try:
            with self.engine.connect() as conn:
                conn.execute(text(f"EXPLAIN {query}"))
            return DryRunResult(valid=True, error=None)
        except Exception as e:
            return DryRunResult(valid=False, error=str(e))

    def explain(self, query: str) -> QueryPlan:
         try:
             with self.engine.connect() as conn:
                 # MySQL JSON format
                 res = conn.execute(text(f"EXPLAIN FORMAT=JSON {query}"))
                 return QueryPlan(original_query=query, plan=str(res.fetchone()[0]))
         except Exception:
             return QueryPlan(original_query=query, plan="Could not retrieve plan")

    def metrics(self) -> ExecutionMetrics:
        return ExecutionMetrics(execution_time_ms=0.0, rows_returned=0)

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            supports_cte=True,
            supports_window_functions=True,
            supports_limit_offset=True,
            supports_multi_db_join=False,
            supports_dry_run=False
        )



    def fetch_schema(self) -> SchemaMetadata:
        inspector = inspect(self.engine)
        tables = []
        table_names = inspector.get_table_names()
        
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
            datasource_id="mysql_ds", 
            tables=tables
        )

    def execute(self, query: str) -> QueryResult:
        with self.engine.connect() as conn:
            result = conn.execute(text(query))
            if result.returns_rows:
                rows = result.fetchall()
                data = [list(row) for row in rows]
                cols = list(result.keys())
                return QueryResult(
                    columns=cols,
                    rows=data,
                    row_count=len(data)
                )
            else:
                 return QueryResult(
                     columns=[], 
                     rows=[], 
                     row_count=result.rowcount
                 )

    def cost_estimate(self, query: str) -> CostEstimate:
        # EXPLAIN FORMAT=JSON could work for MySQL
        return CostEstimate(estimated_cost=10.0, estimated_rows=100) # Stub
