from typing import Dict, Any, List
from sqlalchemy import create_engine, inspect, text, Engine, select, func, table, column, case, literal_column
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
    ColumnStatistics
)
import logging
logger = logging.getLogger(__name__)

class BaseSQLAlchemyAdapter(DatasourceAdapter):
    """
    Base class for all SQLAlchemy-based adapters.
    Implements common logic for connection, execution, and schema fetching.
    """
    def __init__(self, connection_string: str = None, datasource_id: str = None, datasource_engine_type: str = None):
        self.connection_string = connection_string
        self.datasource_id = datasource_id
        self.datasource_engine_type = datasource_engine_type
        self.engine: Engine = None
        if connection_string:
            self.connect()

    def __str__(self):
        return f"{self.datasource_id} ({self.datasource_engine_type})"

    def connect(self) -> None:
        conn_str = self.connection_string
        if not conn_str:
             raise ValueError(f"Connection string is required for {self}")
        try:
            self.engine = create_engine(conn_str, pool_pre_ping=True)
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise


    def execute(self, sql: str) -> QueryResult:
        if not self.engine:
            raise RuntimeError(f"Not connected to {self}")
            
        import time
        start = time.perf_counter()
        
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
            
        duration = time.perf_counter() - start
        return QueryResult(
            columns=cols,
            rows=rows,
            row_count=row_count,
            execution_time_ms=duration * 1000
        )

    def fetch_schema(self) -> SchemaMetadata:
        if not self.engine:
            raise RuntimeError(f"Not connected to {self}. Please verify the connection details.")
            
        inspector = inspect(self.engine)
        tables = []
        
        try:
            table_names = inspector.get_table_names()
        except Exception as e:
            logger.error(f"Failed to fetch table names for {self}: {e}")
            raise


        for table_name in table_names:
            columns = []
            row_count = self._get_row_count(table_name)
            for col_info in inspector.get_columns(table_name):
                columns.append(Column(
                    name=col_info["name"],
                    type=str(col_info["type"]),
                    is_nullable=col_info["nullable"],
                    is_primary_key=col_info.get("primary_key", False),
                    description=col_info.get("comment"),
                    statistics=self._get_column_stats(table_name, col_info["name"], row_count, str(col_info["type"]))
                ))
            
            fks = []
            try:
                for fk_info in inspector.get_foreign_keys(table_name):
                    fks.append(ForeignKey(
                        constrained_columns=fk_info["constrained_columns"],
                        referred_table=fk_info["referred_table"],
                        referred_columns=fk_info["referred_columns"],
                        referred_schema=fk_info.get("referred_schema")
                    ))
            except Exception as e:
                logger.warning(f"Failed to fetch foreign keys for {self}: {e}") 

            try:
                tbl_comment = inspector.get_table_comment(table_name).get("text")
            except Exception:
                logger.warning(f"Failed to fetch table comment for {self}")
                tbl_comment = None

            table_obj = Table(
                name=table_name, 
                columns=columns,
                foreign_keys=fks,
                description=tbl_comment,
                row_count=row_count 
            )

            tables.append(table_obj)
            
        return SchemaMetadata(datasource_id=self.datasource_id, datasource_engine_type=self.datasource_engine_type, tables=tables)

    def _get_row_count(self, table_name: str) -> int:
        """
        Fetches the row count for a specific table.
        """
        try:
            with self.engine.connect() as conn:
                stmt = select(func.count()).select_from(table(table_name))
                return conn.execute(stmt).scalar()
        except Exception as e:
            logger.warning(f"Failed to fetch row count for {self}: {table_name}: {e}")
            return 0

    def _get_column_stats(self, table_name: str, column_name: str, row_count: int, column_type: str) -> ColumnStatistics:
        """
        Fetches statistics for a specific column.
        """
        with self.engine.connect() as conn:
            t = table(table_name)
            c = column(column_name)
            
            stmt = select(
                func.count(case((c == None, 1))),
                func.min(c),
                func.max(c),
                func.count(func.distinct(c))
            ).select_from(t)
            
            result = conn.execute(stmt).fetchone()
            null_count, min_val, max_val, distinct_count = result

            return ColumnStatistics(
                null_percentage=(null_count / row_count) if row_count > 0 else 0,
                distinct_count=distinct_count,
                min_value=min_val,
                max_value=max_val,
                sample_values=self._get_sample_values(table_name, column_name) if any(t in column_type.lower() for t in ['char', 'text', 'string', 'clob']) else []
            )

    def _get_sample_values(self, table_name: str, column_name: str, limit: int = 5) -> List[Any]:
        """
        Fetches sample values for a specific column using SA Core.
        Prioritizes most frequent values.
        """
        t = table(table_name)
        c = column(column_name)

        stmt = (
            select(c)
            .select_from(t)
            .where(c != None)
            .group_by(c)
            .order_by(func.count().desc())
            .limit(limit)
        )
        
        with self.engine.connect() as conn:
            return [row[0] for row in conn.execute(stmt).fetchall()]


    def capabilities(self) -> CapabilitySet:
        return CapabilitySet()
    
    def dry_run(self, sql: str) -> DryRunResult:
        """
        Generic dry run using transaction rollback.
        Works for SQLite, MySQL, Postgres (if not overridden), etc.
        """
        try:
            with self.engine.connect() as conn:
                trans = conn.begin()
                conn.execute(text(sql))
                trans.rollback()
            return DryRunResult(is_valid=True)
        except Exception as e:
            return DryRunResult(is_valid=False, error_message=str(e))

    def explain(self, sql: str) -> QueryPlan:
        return QueryPlan(plan_text="Not implemented")

    def cost_estimate(self, sql: str) -> CostEstimate:
        return CostEstimate(estimated_cost=0.0, estimated_rows=0)
