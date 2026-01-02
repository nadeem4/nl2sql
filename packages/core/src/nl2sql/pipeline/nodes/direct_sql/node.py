from typing import Dict, Any
from langchain_core.prompts import ChatPromptTemplate
from nl2sql.pipeline.state import GraphState
from nl2sql.services.llm import LLMCallable
from nl2sql.datasources import DatasourceRegistry
from .prompts import DIRECT_SQL_PROMPT, DIRECT_SQL_EXAMPLES
from nl2sql.common.logger import get_logger
from nl2sql.common.errors import PipelineError, ErrorSeverity, ErrorCode

logger = get_logger("direct_sql_node")


class DirectSQLNode:
    """Fast Lane Node: Converts User Query + Schema directly to SQL.

    Bypasses Planner/Generator standard loop for simple queries.

    Attributes:
        llm (LLMCallable): The language model to use.
        prompt (ChatPromptTemplate): The prompt template.
        chain (Runnable): The execution chain.
        registry (DatasourceRegistry): The datasource registry.
    """

    def __init__(self, llm_map: dict[str, LLMCallable], registry: DatasourceRegistry):
        """Initializes the DirectSQLNode.

        Args:
            llm_map (dict[str, LLMCallable]): Map of LLMs by name.
            registry (DatasourceRegistry): The datasource registry.
        """
        self.llm = llm_map["direct_sql"]
        self.prompt = ChatPromptTemplate.from_template(DIRECT_SQL_PROMPT)
        self.chain = self.prompt | self.llm
        self.registry = registry

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """Executes the Direct SQL generation.

        Args:
            state (GraphState): The current graph state.

        Returns:
            Dict[str, Any]: Dictionary containing 'sql_draft' and 'reasoning'.
        """
        try:
            relevant_tables = '\n'.join([table.model_dump_json(indent=2) for table in state.relevant_tables])
            dialect = "TSQL"  # Default

            if state.selected_datasource_id:
                try:
                    dialect = self.registry.get_dialect(state.selected_datasource_id)
                except Exception:
                    logger.warning(f"Could not resolve dialect for {state.selected_datasource_id}, defaulting to TSQL")

            # response is now a DirectSQLResponse object
            response = self.chain.invoke({
                "dialect": dialect,
                "relevant_tables": relevant_tables,
                "user_query": state.user_query,
                "examples": DIRECT_SQL_EXAMPLES
            })

            sql = response.sql.strip()
            reasoning = response.reasoning

            logger.info(f"Direct SQL Generated: {sql} | Reasoning: {reasoning}")

            return {
                "sql_draft": sql,
                "reasoning": [{"node": "direct_sql", "content": reasoning}]
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
