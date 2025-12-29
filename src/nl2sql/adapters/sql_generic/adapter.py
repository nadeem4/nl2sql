from typing import Any, List, Optional
from sqlalchemy import create_engine, text, inspect
from nl2sql.adapter_sdk import (
    DataSourceAdapter, AdapterCapabilities, SchemaDefinition,
    ExecutionResult, EstimationResult, TableDefinition, ColumnDefinition
)

class SqlGenericAdapter(DataSourceAdapter):
    """
    Standard SQL Adapter using SQLAlchemy.
    Supports Postgres, MSSQL, SQLite, MySQL, etc.
    """
    
    def __init__(self, connection_string: str):
        self.engine = create_engine(connection_string)
        self._inspector = inspect(self.engine)
        
    def get_capabilities(self) -> AdapterCapabilities:
        # Basic SQL capabilities
        return AdapterCapabilities(
            supports_sql=True,
            supports_transactions=True,
            supports_joins=True,
            supports_hueristic_estimation=True, # We can do EXPLAIN usually
            supported_dialects=["postgres", "tsql", "sqlite", "mysql"]
        )
        
    def get_schema(self, table_names: Optional[List[str]] = None) -> SchemaDefinition:
        all_tables = self._inspector.get_table_names()
        
        target_tables = all_tables
        if table_names:
            target_tables = [t for t in all_tables if t in table_names]
            
        definitions = []
        for t_name in target_tables:
            columns = []
            try:
                cols_meta = self._inspector.get_columns(t_name)
                for c in cols_meta:
                    columns.append(ColumnDefinition(
                        name=c["name"],
                        data_type=str(c["type"]),
                        is_primary_key=c.get("primary_key", False)
                    ))
                
                definitions.append(TableDefinition(
                    name=t_name,
                    columns=columns
                ))
            except Exception as e:
                # Log warning in real app
                pass
                
        return SchemaDefinition(tables=definitions)
        
    def execute(self, query: Any, **kwargs) -> ExecutionResult:
        """
        Executes a SQL Select query.
        """
        if not isinstance(query, str):
            return ExecutionResult(rows=[], column_names=[], row_count=0, error="Query must be a string")
            
        with self.engine.connect() as conn:
            try:
                result = conn.execute(text(query))
                column_names = list(result.keys())
                rows = [dict(row._mapping) for row in result]
                
                return ExecutionResult(
                    rows=rows,
                    column_names=column_names,
                    row_count=len(rows)
                )
            except Exception as e:
                return ExecutionResult(
                    rows=[],
                    column_names=[],
                    row_count=0,
                    error=str(e)
                )
    
    def estimate(self, query: Any) -> EstimationResult:
        """
        Basic estimation using count(*).
        WARN: This re-writes the query. Real implementation should use EXPLAIN.
        """
        if not isinstance(query, str):
             return EstimationResult(0, will_succeed=False, reason="Invalid query")
             
        # Naive implementation: Wrap in select count(*)
        # In production, use EXPLAIN for better performance
        count_query = f"SELECT COUNT(*) FROM ({query}) AS sub"
        
        with self.engine.connect() as conn:
            try:
                result = conn.execute(text(count_query)).scalar()
                return EstimationResult(
                    estimated_row_count=int(result),
                    will_succeed=True
                )
            except Exception as e:
                return EstimationResult(
                    estimated_row_count=0,
                    will_succeed=False,
                    reason=str(e)
                )
