from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from .prompts import CANONICALIZATION_PROMPT

def canonicalize_query(query: str, llm) -> str:
    """
    Rewrites a user query into a canonical, normalized form.
    """
    prompt = PromptTemplate(template=CANONICALIZATION_PROMPT, input_variables=["question"])
    chain = prompt | llm | StrOutputParser()
    try:
        return chain.invoke({"question": query}).strip()
    except Exception as e:
        print(f"  -> Canonicalization failed: {e}")
        return query

__all__ = ["canonicalize_query"]
