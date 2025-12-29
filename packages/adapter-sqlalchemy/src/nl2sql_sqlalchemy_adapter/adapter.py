from typing import Dict, Any, List
from sqlalchemy import create_engine, inspect, text, Engine
from nl2sql_adapter_sdk import (
    DatasourceAdapter, 
    SchemaMetadata, 
    Table, 
    Column, 
    ForeignKey,
    QueryResult, 
    CapabilitySet,
    DryRunResult,
    QueryPlan,
    CostEstimate,
    ExecutionMetrics
)

class BaseSQLAlchemyAdapter(DatasourceAdapter):
    """
    Base class for all SQLAlchemy-based adapters.
    Implements common logic for connection, execution, and schema fetching.
    """
    def __init__(self, connection_string: str = None):
        self.connection_string = connection_string
        self.engine: Engine = None
        if connection_string:
            self.connect({"connection_string": connection_string})

    def connect(self, config: Dict[str, Any]) -> None:
        conn_str = config.get("connection_string") or self.connection_string
        if not conn_str:
             raise ValueError("Connection string is required")
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

    def execute(self, sql: str) -> QueryResult:
        if not self.engine:
            raise RuntimeError("Not connected")
            
        import time
        start = time.time()
        
        with self.engine.connect() as conn:
            result = conn.execute(text(sql))
            if result.returns_rows:
                rows = [list(row) for row in result.fetchall()]
                cols = list(result.keys())
                row_count = len(rows)
            else:
                rows = []
                cols = []
                row_count = result.rowcount
            
        duration = (time.time() - start) * 1000
        return QueryResult(
            columns=cols,
            rows=rows,
            row_count=row_count
        )

    def fetch_schema(self) -> SchemaMetadata:
        if not self.engine:
            raise RuntimeError("Not connected")
            
        inspector = inspect(self.engine)
        tables = []
        
        try:
            table_names = inspector.get_table_names()
        except Exception:
             # Handle empty DB or connection issues gracefully
             table_names = []

        for table_name in table_names:
            columns = []
            for col_info in inspector.get_columns(table_name):
                columns.append(Column(
                    name=col_info["name"],
                    type=str(col_info["type"]),
                    is_nullable=col_info["nullable"],
                    is_primary_key=col_info.get("primary_key", False),
                    description=col_info.get("comment")
                ))
            
            # Fetch Foreign Keys
            fks = []
            try:
                for fk_info in inspector.get_foreign_keys(table_name):
                    fks.append(ForeignKey(
                        constrained_columns=fk_info["constrained_columns"],
                        referred_table=fk_info["referred_table"],
                        referred_columns=fk_info["referred_columns"],
                        referred_schema=fk_info.get("referred_schema")
                    ))
            except Exception:
                pass 

            # Fetch Table Comment
            try:
                tbl_comment = inspector.get_table_comment(table_name).get("text")
            except Exception:
                tbl_comment = None

            tables.append(Table(
                name=table_name, 
                columns=columns,
                foreign_keys=fks,
                description=tbl_comment
            ))
            
        return SchemaMetadata(datasource_id="generic_sqlalchemy", tables=tables)

    def capabilities(self) -> CapabilitySet:
        # Default capabilities, override in subclasses
        return CapabilitySet()
    
    def dry_run(self, sql: str) -> DryRunResult:
        return DryRunResult(is_valid=False, error_message="Not implemented")

    def explain(self, sql: str) -> QueryPlan:
        return QueryPlan(plan_text="Not implemented")

    def cost_estimate(self, sql: str) -> CostEstimate:
        return CostEstimate(estimated_cost=0.0, estimated_rows=0)

    def metrics(self) -> ExecutionMetrics:
        return ExecutionMetrics(execution_ms=0, rows_returned=0, engine="generic")
