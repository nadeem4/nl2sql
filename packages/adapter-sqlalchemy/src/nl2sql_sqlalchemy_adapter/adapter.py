from typing import Dict, Any, List
from sqlalchemy import create_engine, inspect, text, Engine, select, func, table, column, case, literal_column
from nl2sql_adapter_sdk import (
    DatasourceAdapter, 
    SchemaMetadata, 
    Table, 
    Column, 
    ForeignKey,
    QueryResult, 
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
    def __init__(self, datasource_id: str = None, datasource_engine_type: str = None, connection_args: Dict[str, Any] = None, **kwargs):
        """Initializes the SQLAlchemy adapter.

        Args:
            datasource_id (str, optional): The unique identifier for the datasource.
            datasource_engine_type (str, optional): The engine type (e.g., 'postgres').
            connection_args (Dict[str, Any], optional): The resolved connection arguments.
            **kwargs: Additional configuration parameters like 'row_limit' and 'max_bytes'.
        """
        self._datasource_id = datasource_id
        self.datasource_engine_type = datasource_engine_type
        self._row_limit = kwargs.get("row_limit")
        self._max_bytes = kwargs.get("max_bytes")
        
        self.statement_timeout_ms = kwargs.get("statement_timeout_ms")
        self.execution_options = {}
        
        if self.statement_timeout_ms:
            self.execution_options["timeout"] = self.statement_timeout_ms / 1000.0

        self.connection_string = self.construct_uri(connection_args)

        self.engine: Engine = None
        if self.connection_string:
            self.connect()

    @property
    def datasource_id(self) -> str:
        return self._datasource_id

    @property
    def row_limit(self) -> int:
        return self._row_limit

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def __str__(self):
        return f"{self.datasource_id} ({self.datasource_engine_type})"

    def construct_uri(self, args: Dict[str, Any]) -> str:
        """
        Constructs a SQLAlchemy URL from a connection dictionary.
        Must be implemented by subclasses.
        """
        raise NotImplementedError(f"Adapter {self.__class__.__name__} must implement construct_uri")

    def connect(self) -> None:
        """Establishes a connection to the database.

        Raises:
            ValueError: If the connection string is missing.
            Exception: If connection fails.
        """
        conn_str = self.connection_string
        if not conn_str:
             raise ValueError(f"Connection string is required for {self}")
        try:
            self.engine = create_engine(
                conn_str, 
                pool_pre_ping=True,
                execution_options=self.execution_options
            )
        except Exception as e:
            logger.error(f"Failed to connect to database: {e}")
            raise


    def execute(self, sql: str) -> QueryResult:
        """Executes a SQL query against the datasource.

        Args:
            sql (str): The SQL query string to execute.

        Returns:
            QueryResult: The results of the query execution.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
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
        
        total_bytes = 0
        if row_count > 0:
            sample_size = min(50, row_count)
            sample_bytes = 0
            for i in range(sample_size):
                row = rows[i]
                for item in row:
                    if item is not None:
                        sample_bytes += len(str(item))
            
            avg_row_bytes = sample_bytes / sample_size
            total_bytes = int(avg_row_bytes * row_count)

        return QueryResult(
            columns=cols,
            rows=rows,
            row_count=row_count,
            execution_time_ms=duration * 1000,
            bytes_returned=total_bytes
        )

    def fetch_schema(self) -> SchemaMetadata:
        """Fetches the schema metadata for the connected datasource.

        Returns:
            SchemaMetadata: The metadata containing tables, columns, and relationships.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
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
        """Fetches the total row count for a specific table.

        Args:
            table_name (str): The name of the table to count.

        Returns:
            int: The total number of rows.
        """
        try:
            with self.engine.connect() as conn:
                stmt = select(func.count()).select_from(table(table_name))
                return conn.execute(stmt).scalar()
        except Exception as e:
            logger.warning(f"Failed to fetch row count for {self}: {table_name}: {e}")
            return 0

    def _get_column_stats(self, table_name: str, column_name: str, row_count: int, column_type: str) -> ColumnStatistics:
        """Fetches statistics for a specific column.

        Args:
            table_name (str): The table name.
            column_name (str): The column name.
            row_count (int): Total rows in the table.
            column_type (str): The column type string.

        Returns:
            ColumnStatistics: Statistical data about the column.
        """
        if any(x in column_type.lower() for x in ['json', 'blob', 'binary', 'bytea', 'xml', 'array']):
             return ColumnStatistics(
                null_percentage=0.0,
                distinct_count=0,
                min_value=None,
                max_value=None,
                sample_values=[]
            )

        with self.engine.connect() as conn:
            t = table(table_name)
            c = column(column_name)
            
            stmt = (
                select(
                    func.count(case((c == None, 1))),
                    func.min(c),
                    func.max(c),
                    func.count(func.distinct(c))
                )
                .select_from(t)
            )
            
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
        """Fetches frequently occurring sample values for a column.

        Args:
            table_name (str): The table name.
            column_name (str): The column name.
            limit (int, optional): Max samples to return. Defaults to 5.

        Returns:
            List[Any]: A list of sample values.
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
    
    def get_dialect(self) -> str:
        """Returns the dialect name. Defaults to engine type."""
        return (self.datasource_engine_type or "unknown").lower()

    def cost_estimate(self, sql: str) -> CostEstimate:
        return CostEstimate(estimated_cost=0.0, estimated_rows=0)
