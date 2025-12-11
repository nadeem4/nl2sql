from typing import List, Optional, Tuple
from langchain_core.prompts import PromptTemplate
from nl2sql.datasource_config import DatasourceProfile
from .prompts import ROUTER_PROMPT
from .schemas import RouterDecision

def decided_best_datasource(query: str, llm, datasources: List[DatasourceProfile]) -> Tuple[Optional[str], str]:
    """
    Uses an LLM to reason about which datasource is best (Layer 3).
    Returns: (datasource_id, reasoning)
    """
    # Format datasource descriptions
    iterable = datasources.values() if isinstance(datasources, dict) else datasources
    ds_context = "\n".join([f"- ID: {ds.id}\n  Description: {ds.description}" for ds in iterable])

    prompt = PromptTemplate(template=ROUTER_PROMPT, input_variables=["context", "question"])
    
    # Use Structured Output
    structured_llm = llm.with_structured_output(RouterDecision)
    chain = prompt | structured_llm
    
    try:
        decision: RouterDecision = chain.invoke({"context": ds_context, "question": query})
        
        ds_id = decision.datasource_id
        reasoning = decision.reasoning
        
        # Normalize "None" string or actual None
        if ds_id and ds_id.lower() == "none":
            ds_id = None

        # Validate ID exists
        valid_ids = {ds.id for ds in iterable}
        
        if ds_id and ds_id in valid_ids:
            return ds_id, reasoning
        
        if ds_id is None:
             return None, reasoning

        return None, f"Invalid ID generated: {ds_id}. Reasoning: {reasoning}"
        
    except Exception as e:
        print(f"  -> LLM routing failed: {e}")
        return None, f"Error: {str(e)}"

__all__ = ["decided_best_datasource"]
