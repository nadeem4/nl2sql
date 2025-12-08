MULTI_QUERY_PROMPT = """You are an AI search assistant. Your task is to generate 3 DISTINCT versions of the user question to maximize retrieval coverage.
You must generate queries from different perspectives:

1. **Specific/Technical**: Use precise terms (e.g., "inventory count", "schema definition").
2. **Broad/Intent**: Focus on the user's goal (e.g., "stock availability", "find table").
3. **Hypothetical/Edge-case**: Ask about related entities or conditions.

Return *only* the 3 questions, separated by newlines.

Original question: {question}"""
