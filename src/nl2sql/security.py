import sqlglot
from sqlglot import expressions as exp

def enforce_read_only(sql: str) -> bool:
    """
    Checks if the SQL query is strictly read-only using sqlglot AST analysis.
    Returns True if the query contains only SELECT statements (and CTEs), False otherwise.
    """
    try:
        statements = sqlglot.parse(sql)
        
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
        # If parsing fails, fail safe (reject)
        return False
