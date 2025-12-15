from __future__ import annotations

import json
from typing import Callable, Optional, Union, Dict, Any

from langchain_core.runnables import Runnable

from nl2sql.schemas import GraphState
from nl2sql.nodes.summarizer.prompts import SUMMARIZER_PROMPT

from nl2sql.logger import get_logger

logger = get_logger("summarizer")

LLMCallable = Union[Callable[[str], str], Runnable]

from langchain_core.prompts import ChatPromptTemplate

from langchain_core.output_parsers import StrOutputParser

class SummarizerNode:
    """
    Analyzes validation errors and generates constructive feedback for the Planner.

    Uses an LLM to look at the failed plan, the schema, and the errors to suggest fixes.
    """

    def __init__(self, llm: Optional[LLMCallable] = None):
        """
        Initializes the SummarizerNode.

        Args:
            llm: The language model to use for summarization.
        """
        self.llm = llm
        if self.llm:
            self.prompt = ChatPromptTemplate.from_template(SUMMARIZER_PROMPT)
            self.chain = self.prompt | self.llm | StrOutputParser()

    def __call__(self, state: GraphState) -> Dict[str, Any]:
        """
        Executes the summarization step.

        Args:
            state: The current graph state.

        Returns:
            Dictionary updates for the graph state with refined error messages (feedback).
        """
        node_name = "summarizer"

        try:
            if not self.llm:
                return {"errors": ["Summarizer LLM not provided."]}

            schema_context = ""
            if state.schema_info:
                lines = []
                for tbl in state.schema_info.tables:
                    lines.append(f"Table: {tbl.name} (Alias: {tbl.alias})")
                    lines.append(f"  Columns: {', '.join([f'{c.name} ({c.type})' for c in tbl.columns])}")
                    lines.append("")
                schema_context = "\n".join(lines)

            failed_plan_str = "No plan generated."
            if state.plan:
                try:
                    failed_plan_str = json.dumps(state.plan, indent=2)
                except:
                    failed_plan_str = str(state.plan)

            errors_str = "\n".join(f"- {e}" for e in state.errors)

            try:
                feedback = self.chain.invoke({
                    "user_query": state.user_query,
                    "schema_context": schema_context,
                    "failed_plan": failed_plan_str,
                    "errors": errors_str
                })
                
                return {
                    "errors": [feedback],
                    "reasoning": {"summarizer": [feedback]}
                }
            except Exception as e:
                    raise e
                     
        except Exception as e:
            logger.error(f"Node {node_name} failed: {e}")
            return {
                "reasoning": {"summarizer": [f"Summarizer failed: {e}"]}
            }
