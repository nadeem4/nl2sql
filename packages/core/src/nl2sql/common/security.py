"""Security utilities for SQL validation."""
import sqlglot
from sqlglot import expressions as exp


def enforce_read_only(sql: str, dialect: str | None = None) -> bool:
    """Checks if the SQL query is strictly read-only using sqlglot AST analysis.

    Ensures that the query contains only SELECT statements (including CTEs and UNIONs)
    and does not contain any DML or DDL statements.

    Args:
        sql (str): The SQL query string to validate.
        dialect (Optional[str]): Optional SQL dialect (e.g., "tsql", "postgres") for parsing.

    Returns:
        bool: True if the query is read-only, False otherwise.
    """
    try:
        statements = sqlglot.parse(sql, read=dialect)
        
        for statement in statements:
            # Allow: SELECT, UNION, WITH (CTE) followed by SELECT
            if not isinstance(statement, (exp.Select, exp.Union, exp.Subquery)):
                return False
                
            forbidden_types = (
                exp.Insert, exp.Update, exp.Delete, exp.Drop, 
                exp.AlterTable, exp.TruncateTable, exp.Command, exp.Create
            )
            
            if statement.find(forbidden_types):
                return False
                
        return True
    except Exception:
        return False
