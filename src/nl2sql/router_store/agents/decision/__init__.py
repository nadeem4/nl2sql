from typing import List, Optional, Tuple
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from nl2sql.datasource_config import DatasourceProfile
from .prompts import ROUTER_PROMPT

def decided_best_datasource(query: str, llm, datasources: List[DatasourceProfile]) -> Tuple[Optional[str], str]:
    """
    Uses an LLM to reason about which datasource is best (Layer 3).
    Returns: (datasource_id, reasoning)
    """
    # Format datasource descriptions
    iterable = datasources.values() if isinstance(datasources, dict) else datasources
    ds_context = "\n".join([f"- ID: {ds.id}\n  Description: {ds.description}" for ds in iterable])

    prompt = PromptTemplate(template=ROUTER_PROMPT, input_variables=["context", "question"])
    chain = prompt | llm | StrOutputParser()
    
    try:
        result = chain.invoke({"context": ds_context, "question": query}).strip()
        lines = result.split("\n")
        reasoning = "No reasoning extracted."
        ds_id = None
        
        for line in lines:
            if line.startswith("Reasoning:"):
                reasoning = line.replace("Reasoning:", "").strip()
            elif line.startswith("ID:"):
                ds_id_raw = line.replace("ID:", "").strip()
                # Clean up
                ds_id = ds_id_raw.split()[0].strip().strip('"').strip("'")
        
        # Fallback simple parse if format missed
        if not ds_id and not result.startswith("Reasoning"):
                ds_id = result.split()[0].strip().strip('"').strip("'")

        iterable = datasources.values() if isinstance(datasources, dict) else datasources
        valid_ids = {ds.id for ds in iterable}
        
        if ds_id in valid_ids:
            return ds_id, reasoning
            
        return None, f"Invalid ID generated: {ds_id}. Reasoning: {reasoning}"
        
    except Exception as e:
        print(f"  -> LLM routing failed: {e}")
        print(f"Error: {e}")
        return None, f"Error: {str(e)}"

__all__ = ["decided_best_datasource"]
