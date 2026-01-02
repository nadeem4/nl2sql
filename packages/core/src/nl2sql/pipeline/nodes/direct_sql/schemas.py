from pydantic import BaseModel, Field

class DirectSQLResponse(BaseModel):
    """Structured response for Direct SQL generation."""
    
    reasoning: str = Field(
        description="Brief explanation of how the query was constructed, including schema choices and aliases used."
    )
    sql: str = Field(
        description="The valid SQL query to execute. Must use explicit aliases and valid dialect syntax."
    )
