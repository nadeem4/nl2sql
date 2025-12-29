from typing import Dict, Any
from langchain_core.prompts import ChatPromptTemplate
from nl2sql.core.schemas import GraphState
from nl2sql.core.llm_registry import LLMCallable
from nl2sql.core.datasource_registry import DatasourceRegistry
from .prompts import DIRECT_SQL_PROMPT
from nl2sql.core.logger import get_logger
from nl2sql.core.errors import PipelineError, ErrorSeverity, ErrorCode

logger = get_logger("direct_sql_node")

class DirectSQLNode:
    """
    Fast Lane Node: Converts User Query + Schema directly to SQL.
    Bypasses Planner/Generator standard loop for simple queries.
    """
    def __init__(self, llm_map: dict[str, LLMCallable], registry: DatasourceRegistry):
        self.llm = llm_map["direct_sql"]
        self.prompt = ChatPromptTemplate.from_template(DIRECT_SQL_PROMPT)
        self.chain = self.prompt | self.llm
        self.registry = registry

    def _format_schema(self, state: GraphState) -> str:
        """Formats the retrieved schema info into a string."""
        if not state.schema_info:
            return "No schema information available."
        
        lines = []
        for table in state.schema_info.tables:
            cols = ", ".join([f"{c.original_name} ({c.type})" for c in table.columns])
            lines.append(f"Table: {table.name}\nColumns: {cols}")
            if table.foreign_keys:
                fks = [f"{fk.column} -> {fk.referred_table}.{fk.referred_column}" for fk in table.foreign_keys]
                lines.append(f"Foreign Keys: {', '.join(fks)}")
            lines.append("---")
        return "\n".join(lines)

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        try:
            schema_str = self._format_schema(state)
            dialect = "TSQL" # Default
            
            if state.selected_datasource_id:
                try:
                    profile = self.registry.get_profile(state.selected_datasource_id)
                    if "postgres" in profile.engine:
                        dialect = "PostgreSQL"
                    elif "mysql" in profile.engine:
                        dialect = "MySQL"
                    elif "sqlite" in profile.engine:
                        dialect = "SQLite"
                    elif "oracle" in profile.engine:
                        dialect = "Oracle"
                except Exception:
                    logger.warning(f"Could not resolve dialect for {state.selected_datasource_id}, defaulting to TSQL")
            
            response = self.chain.invoke({
                "dialect": dialect,
                "schema_info": schema_str,
                "user_query": state.user_query
            })

            # Extract content from AIMessage if needed
            response_content = response.content if hasattr(response, "content") else str(response)

            # Clean output (remove markdown if model hallucinates it despite instructions)
            sql = response_content.replace("```sql", "").replace("```", "").strip()
            
            logger.info(f"Direct SQL Generated: {sql}")

            return {
                "sql_draft": sql,
                "reasoning": [{"node": "direct_sql", "content": "Fast Lane generation successful."}]
            }

        except Exception as e:
            return {
                "errors": [
                    PipelineError(
                        node="direct_sql",
                        message=f"Direct SQL generation failed: {str(e)}",
                        severity=ErrorSeverity.ERROR,
                        error_code=ErrorCode.SQL_GEN_FAILED,
                        stack_trace=str(e)
                    )
                ]
            }
