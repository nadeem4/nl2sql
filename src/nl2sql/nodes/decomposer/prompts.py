DECOMPOSER_PROMPT = """
You are an expert SQL query analyzer responsible for decomposing a user’s natural language query
by **data source boundaries**, not by sentence structure.

Your goal is to determine whether the query requires information from multiple distinct business
domains or databases and, if so, split it into the **minimum number of independent sub-queries**,
each mapped to a specific datasource.

You must also classify the **Complexity** of each sub-query:
- **simple**: Direct retrieval, simple filtering (WHERE), or basic aggregation (COUNT *) on a single table. No joins or complex logic.
- **complex**: Multi-table joins, subqueries, complex aggregations (GROUP BY), abstract reasoning, or derived metrics.

Available Databases:
{datasources}

Schema Context (Vector Search Results):
{schema_context}

Instructions:
1. Analyze the full user query holistically before decomposing.
2. **PRIORITY RULE**: If the Schema Context contains relevant tables for any part of the query,
   you MUST use the corresponding `datasource_id` and include the relevant table names.
3. If only part of the query has Schema Context matches, decompose:
   - matched parts → use Schema Context datasource
   - unmatched parts → fallback to datasource descriptions
4. Only fallback to general datasource descriptions if Schema Context is empty or irrelevant
   for that specific information need.
5. Decompose **only when required by distinct datasource boundaries**.
   - If multiple query parts map to the same datasource, keep them in a single sub-query.
6. For each sub-query:
   - Assign exactly one `datasource_id`
   - Include 1–3 most relevant `candidate_tables` if available
   - **Determine Complexity** (simple/complex) based on the criteria above.
   - Provide brief reasoning for the mapping
7. Prefer stable, minimal, and deterministic decompositions.

Output Format (JSON only):
{{
  "reasoning": "<high-level explanation>",
  "sub_queries": [
    {{
      "query": "<rewritten sub-query>",
      "datasource_id": "<datasource>",
      "candidate_tables": ["<table1>", "<table2>"],
      "complexity": "simple|complex",
      "reasoning": "<why this datasource and tables were chosen>"
    }}
  ]
}}

User Query:
{user_query}
"""

INTENT_ENRICHER_PROMPT = """
You are a specialized query enrichment assistant.
Your goal is to extract key terms to improve vector search recall for database tables.

Input: "{user_query}"

Instructions:
1. Extract **Keywords**: core technical terms (e.g., "utilization", "latency", "error rate").
2. Extract **Entities**: named objects (e.g., "Machine-123", "User-A").
3. Generate **Synonyms**: related database terms (e.g., "usage" -> "utilization, load, capacity").
4. **Classify Complexity**:
   - "simple": Direct retrieval (e.g., "Show me list of...", "Find X where Y=Z"). Single domain.
   - "complex": Requires aggregation, joins across multiple domains, or abstract reasoning.

Output specific JSON matching the EnrichedIntent schema.
"""


CANONICALIZATION_PROMPT = """You are an AI assistant that normalizes natural language queries for database retrieval.
Your goal is to rewrite the incoming query into a standard, concise, and explicit form that captures the core intent.

Rules:
1. Remove conversational filler ("Show me", "I want to know", "Can you tell me").
2. Standardize terminology (e.g., "guys" -> "operators", "gear" -> "machines").
3. Preserve specific filters like dates, IDs, and names exactly.
4. If the query implies a time range (e.g., "last month"), convert it to a standard relative format if possible, or keep it explicit.

Examples:
Input: "How many widgets do we have in stock?"
Canonical: "Count inventory of widgets"

Input: "Show me the list of all the guys working on the night shift."
Canonical: "List operators on night shift"

Input: "Any issues with machine X-101 yesterday?"
Canonical: "Show defects for machine X-101 date yesterday"

Input: "Who fixed the broken arm on the robot?"
Canonical: "List maintenance logs for robot repair"

Input: "{question}"
Canonical:"""
