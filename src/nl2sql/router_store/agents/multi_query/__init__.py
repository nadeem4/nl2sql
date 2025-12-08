from typing import List
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from .prompts import MULTI_QUERY_PROMPT

def generate_query_variations(query: str, llm) -> List[str]:
    """
    Generates 3 different versions of the user question for multi-query retrieval.
    """
    prompt = PromptTemplate(template=MULTI_QUERY_PROMPT, input_variables=["question"])
    chain = prompt | llm | StrOutputParser()
    
    try:
        variations = chain.invoke({"question": query}).split("\n")
        variations = [v.strip() for v in variations if v.strip()]
        return variations
    except Exception as e:
        print(f"  -> Multi-query generation failed: {e}")
        return []

__all__ = ["generate_query_variations"]
