from typing import List
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import StrOutputParser
from .prompts import ENRICHMENT_PROMPT

def enrich_question(question: str, llm) -> List[str]:
    """
    Generates synonyms and semantic variations for a question to improve retrieval coverage.
    """
    prompt = PromptTemplate(template=ENRICHMENT_PROMPT, input_variables=["question"])
    chain = prompt | llm | StrOutputParser()
    try:
        result = chain.invoke({"question": question})
        variations = [v.strip().lstrip("- ").strip() for v in result.split("\n") if v.strip()]
        return variations
    except Exception as e:
        print(f"  -> Enrichment failed: {e}")
        return []

__all__ = ["enrich_question"]
