from typing import Dict, Any
from sqlalchemy import create_engine, inspect, text
from nl2sql_adapter_sdk import (
    DatasourceAdapter, 
    SchemaMetadata, 
    Table, 
    Column, 
    QueryResult, 
    CapabilitySet,
    DryRunResult,
    QueryPlan,
    CostEstimate,
    ExecutionMetrics
)

class PostgresAdapter(DatasourceAdapter):
    def __init__(self, connection_string: str = None):
        self.engine = None
        if connection_string:
            self.connect({"connection_string": connection_string})
        
    def connect(self, config: Dict[str, Any]) -> None:
        """
        Connects using SQLAlchemy.
        Expects config['connection_string'].
        """
        conn_str = config.get("connection_string")
        if not conn_str:
            raise ValueError("Missing 'connection_string' in postgres config")
        self.engine = create_engine(conn_str)
        
    def validate_connection(self) -> bool:
        if not self.engine:
            return False
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            supports_cte=True,
            supports_window_functions=True,
            supports_limit_offset=True,
            supports_dry_run=True  # via EXPLAIN
        )

    def fetch_schema(self) -> SchemaMetadata:
        if not self.engine:
            raise RuntimeError("Not connected")
            
        inspector = inspect(self.engine)
        tables = []
        
        for table_name in inspector.get_table_names():
            columns = []
            for col_info in inspector.get_columns(table_name):
                columns.append(Column(
                    name=col_info["name"],
                    type=str(col_info["type"]),
                    is_nullable=col_info["nullable"],
                    is_primary_key=col_info.get("primary_key", False)
                ))
            tables.append(Table(name=table_name, columns=columns))
            
        return SchemaMetadata(datasource_id="postgres", tables=tables)

    def execute(self, sql: str) -> QueryResult:
        if not self.engine:
            raise RuntimeError("Not connected")
            
        import time
        start = time.time()
        
        with self.engine.connect() as conn:
            result = conn.execute(text(sql))
            rows = [list(row) for row in result.fetchall()]
            cols = list(result.keys())
            
        duration = (time.time() - start) * 1000
        return QueryResult(
            columns=cols,
            rows=rows,
            row_count=len(rows)
        )

    def dry_run(self, sql: str) -> DryRunResult:
        # Simple dry run via EXPLAIN
        try:
            self.execute(f"EXPLAIN {sql}")
            return DryRunResult(is_valid=True)
        except Exception as e:
            return DryRunResult(is_valid=False, error_message=str(e))

    def explain(self, sql: str) -> QueryPlan:
        res = self.execute(f"EXPLAIN (FORMAT JSON) {sql}")
        return QueryPlan(plan_text=str(res.rows))

    def cost_estimate(self, sql: str) -> CostEstimate:
        return CostEstimate(estimated_cost=0.0, estimated_rows=0)

    def metrics(self) -> ExecutionMetrics:
        return ExecutionMetrics(execution_ms=0, rows_returned=0, engine="postgres")
