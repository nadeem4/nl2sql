from typing import List
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from nl2sql.pipeline.nodes.intent.schemas import IntentResponse
from nl2sql.pipeline.nodes.intent.prompts import INTENT_PROMPT

def canonicalize_query(query: str, llm) -> str:
    """
    Uses the IntentPrompt to get the canonical form of the query.
    """
    prompt = ChatPromptTemplate.from_template(INTENT_PROMPT)
    chain = prompt | llm.with_structured_output(IntentResponse)
    
    try:
        response: IntentResponse = chain.invoke({"user_query": query})
        return response.canonical_query
    except Exception as e:
        print(f"Canonicalization failed: {e}")
        return query

def enrich_question(query: str, llm) -> List[str]:
    """
    Uses the IntentPrompt to get keywords, entities, and synonyms.
    Returns a list of strings to be added to the vector index.
    """
    prompt = ChatPromptTemplate.from_template(INTENT_PROMPT)
    chain = prompt | llm.with_structured_output(IntentResponse)
    
    try:
        response: IntentResponse = chain.invoke({"user_query": query})
        terms = []
        if response.keywords:
            terms.extend(response.keywords)
        if response.entities:
            terms.extend(response.entities)
        if response.synonyms:
            terms.extend(response.synonyms)
        return terms
    except Exception as e:
        print(f"Enrichment failed: {e}")
        return []
