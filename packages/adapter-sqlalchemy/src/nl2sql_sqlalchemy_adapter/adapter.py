from typing import Dict, Any, List
from sqlalchemy import create_engine, inspect, text, Engine, select, func, table, column, case, literal_column, Connection
from sqlalchemy.engine.reflection import Inspector
from typing import Tuple
from nl2sql_adapter_sdk.capabilities import DatasourceCapability
from nl2sql_adapter_sdk.contracts import (
    AdapterRequest,
    ResultError,
    ResultFrame,
)
from .models import DryRunResult, QueryPlan, CostEstimate
from nl2sql_adapter_sdk.schema import (
    SchemaContract,
    SchemaMetadata,
    TableContract,
    ColumnContract,
    ForeignKeyContract,
    ColumnMetadata,
    TableMetadata,
    SchemaSnapshot,
    ColumnStatistics,
    TableRef,
)
import logging
logger = logging.getLogger(__name__)

class BaseSQLAlchemyAdapter:
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

        self.connection_args = connection_args
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


    def capabilities(self) -> set[DatasourceCapability]:
        """Default capability set for SQL adapters."""
        return {
            DatasourceCapability.SUPPORTS_SQL,
            DatasourceCapability.SUPPORTS_SCHEMA_INTROSPECTION,
            DatasourceCapability.SUPPORTS_DRY_RUN,
            DatasourceCapability.SUPPORTS_COST_ESTIMATE,
        }

    def execute_sql(self, sql: str) -> ResultFrame:
        """Executes a SQL query against the datasource.

        Args:
            sql (str): The SQL query string to execute.

        Returns:
            ResultFrame: The results of the query execution.

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

        return ResultFrame(
            success=True,
            columns=cols,
            rows=rows,
            row_count=row_count,
            bytes=total_bytes,
            datasource_id=self.datasource_id,
            execution_stats={"execution_time_ms": duration * 1000},
        )

    def execute(self, request: AdapterRequest) -> ResultFrame:
        """Executes an adapter request and returns a ResultFrame."""
        if request.plan_type.lower() != "sql":
            return ResultFrame(
                success=False,
                datasource_id=self.datasource_id,
                error=ResultError(
                    error_code="CAPABILITY_VIOLATION",
                    safe_message="SQL adapter received non-SQL request.",
                    severity="ERROR",
                    retryable=False,
                    stage="adapter",
                    datasource_id=self.datasource_id,
                ),
            )

        sql = request.payload.get("sql")
        if not sql:
            return ResultFrame(
                success=False,
                datasource_id=self.datasource_id,
                error=ResultError(
                    error_code="MISSING_SQL",
                    safe_message="SQL adapter received an empty SQL payload.",
                    severity="ERROR",
                    retryable=False,
                    stage="adapter",
                    datasource_id=self.datasource_id,
                ),
            )

        return self.execute_sql(sql)


    @property
    def exclude_schemas(self) -> set[str]:
        raise NotImplementedError(f"Adapter {self.__class__.__name__} must implement exclude_schemas")


    def fetch_schema_contact(self) -> SchemaContract:
        """Fetches the schema contract for the connected datasource.

        Returns:
            SchemaContract: The contract containing tables, columns, and relationships.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        if not self.engine:
            raise RuntimeError(f"Not connected to {self}. Please verify the connection details.")

        exclude_schemas = {schema.lower() for schema in self.exclude_schemas}
        inspector = inspect(self.engine)
        tables_contract = {}

        try:
            schemas = [schema for schema in inspector.get_schema_names() if schema.lower() not in exclude_schemas]
        except Exception as e:
            logger.error(f"Failed to fetch table names for {self}: {e}")
            raise

        with self.engine.connect() as conn:
            for schema in sorted(schemas):
                table_names = inspector.get_table_names(schema=schema)
                for table_name in sorted(table_names):
                    table_ref = TableRef(schema_name=schema, table_name=table_name)
                    columns_contract = self._get_column_contract(inspector, conn, table_ref)
                    fks = self._get_fk_cols(inspector, table_ref)

                    table_contract = TableContract(
                        table=table_ref,
                        columns=columns_contract,
                        foreign_keys=fks
                    )
                    tables_contract[table_ref.full_name] = table_contract

        return SchemaContract(
            datasource_id=self.datasource_id,
            engine_type=self.datasource_engine_type,
            tables=tables_contract
        )

    def fetch_schema_metadata(self) -> SchemaMetadata:
        """Fetches the schema metadata for the connected datasource.

        Returns:
            SchemaMetadata: The metadata containing tables, columns, and relationships.

        Raises:
            RuntimeError: If the adapter is not connected.
        """
        if not self.engine:
            raise RuntimeError(f"Not connected to {self}. Please verify the connection details.")

        exclude_schemas = {schema.lower() for schema in self.exclude_schemas}
        inspector = inspect(self.engine)
        tables_metadata = {}

        try:
            schemas = [schema for schema in inspector.get_schema_names() if schema.lower() not in exclude_schemas]
        except Exception as e:
            logger.error(f"Failed to fetch table names for {self}: {e}")
            raise

        with self.engine.connect() as conn:
            for schema in sorted(schemas):
                table_names = inspector.get_table_names(schema=schema)
                for table_name in sorted(table_names):
                    table_ref = TableRef(schema_name=schema, table_name=table_name)
                    row_count = self._get_row_count(conn, table_ref)
                    columns_metadata = self._get_columns_metadata(inspector, conn, table_ref, row_count)
                    try:
                        table_comment = inspector.get_table_comment(
                            table_ref.table_name,
                            schema=table_ref.schema_name
                        ).get("text")
                    except Exception:
                        table_comment = None

                    table_metadata = TableMetadata(
                        table=table_ref,
                        columns=columns_metadata,
                        row_count=row_count,
                        description=table_comment
                    )
                    tables_metadata[table_ref.full_name] = table_metadata

        return SchemaMetadata(
            datasource_id=self.datasource_id,
            engine_type=self.datasource_engine_type,
            description="",
            domains=[],
            tables=tables_metadata
        )

    def fetch_schema_snapshot(self) -> SchemaSnapshot:
        """Fetches the schema metadata for the connected datasource.

        Returns:
            SchemaMetadata: The metadata containing tables, columns, and relationships.

        Raises:
            RuntimeError: If the adapter is not connected.
        """

        schema_contract = self.fetch_schema_contact()
        schema_metadata = self.fetch_schema_metadata()

        return SchemaSnapshot(contract=schema_contract, metadata=schema_metadata)


    def _get_columns_metadata(self, inspector: Inspector, conn: Connection, table_ref: TableRef, row_count: int) -> Dict[str, ColumnMetadata]:
        columns_metadata = {}

        for col_info in inspector.get_columns(table_ref.table_name, schema=table_ref.schema_name):
            columns_metadata[col_info["name"]] = ColumnMetadata(
                description=col_info.get("comment"),
                statistics=self._get_column_stats(conn, table_ref, col_info["name"], row_count, str(col_info["type"])),
                synonyms=[],
                pii=False
            )

        return columns_metadata

    def _get_column_contract(self, inspector: Inspector, conn: Connection, table_ref: TableRef) -> Dict[str, ColumnContract]:
        columns_contract = {}
        pk_cols = set(inspector.get_pk_constraint(table_ref.table_name, schema=table_ref.schema_name).get("constrained_columns") or [])
        for col_info in inspector.get_columns(table_ref.table_name, schema=table_ref.schema_name):
            columns_contract[col_info["name"]] = ColumnContract(
                name=col_info["name"],
                data_type=str(col_info["type"]),
                is_nullable=bool(col_info["nullable"]),
                is_primary_key=col_info["name"] in pk_cols
            )
        return columns_contract

    def _get_fk_cols(self, inspector: Inspector, table_ref: TableRef) -> List[ForeignKeyContract]:
        fks = []
        try:
            for fk_info in inspector.get_foreign_keys(table_ref.table_name, schema=table_ref.schema_name):
                fks.append(ForeignKeyContract(
                    constrained_columns=fk_info["constrained_columns"],
                    referred_table=TableRef(schema_name=fk_info.get("referred_schema"), table_name=fk_info["referred_table"]),
                    referred_columns=fk_info["referred_columns"],
                ))
        except Exception as e:
            logger.warning(f"Failed to fetch foreign keys for {self}: {e}") 
        return fks

    def _get_row_count(self, conn: Connection, table_ref: TableRef) -> int:
        """Fetches the total row count for a specific table.

        Args:
            table_ref (TableRef): The table reference.

        Returns:
            int: The total number of rows.
        """
        try:
            stmt = select(func.count()).select_from(table(table_ref.table_name, schema=table_ref.schema_name))
            return conn.execute(stmt).scalar()
        except Exception as e:
            logger.warning(f"Failed to fetch row count for {self}: {table_ref.full_name}: {e}")
            return 0

    def _get_column_stats(self, conn: Connection, table_ref: TableRef, column_name: str, row_count: int, column_type: str) -> ColumnStatistics:
        """Fetches statistics for a specific column.

        Args:
            table_ref (TableRef): The table reference.
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

        t = table(table_ref.table_name, column(column_name), schema=table_ref.schema_name)
        c = t.c[column_name]
        
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
            sample_values=self._get_sample_values(conn, table_ref, column_name) if any(t in column_type.lower() for t in ['char', 'text', 'string', 'clob']) else []
        )

    def _get_sample_values(self, conn: Connection, table_ref: TableRef, column_name: str, limit: int = 5) -> List[Any]:
        """Fetches frequently occurring sample values for a column.

        Args:
            table_name (str): The table name.
            column_name (str): The column name.
            limit (int, optional): Max samples to return. Defaults to 5.

        Returns:
            List[Any]: A list of sample values.
        """
        t = table(table_ref.table_name, column(column_name), schema=table_ref.schema_name)
        c = t.c[column_name]

        stmt = (
            select(c)
            .select_from(t)
            .where(c != None)
            .group_by(c)
            .order_by(func.count().desc())
            .limit(limit)
        )
        
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
        raise NotImplementedError(f"Adapter {self.__class__.__name__} must implement explain")
    
    def get_dialect(self) -> str:
        raise NotImplementedError(f"Adapter {self.__class__.__name__} must implement get_dialect")

    def cost_estimate(self, sql: str) -> CostEstimate:
        raise NotImplementedError(f"Adapter {self.__class__.__name__} must implement cost_estimate")
    

    def test_connection(self) -> bool:
        """
        Tests the database connection by executing a simple query.
        """
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"Connection test failed for {self}: {e}")
            return False
        
