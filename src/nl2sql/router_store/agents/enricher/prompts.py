ENRICHMENT_PROMPT = """You are an AI assistant helping to build a search index.
Generate 3-5 diverse semantic variations of the following user question.
Include different phrasings, synonyms, and keywords that a user might realistically use to ask for the same information.
Keep the variations concise. Return them as a newline-separated list.

Question: "{question}"
Variations:"""
