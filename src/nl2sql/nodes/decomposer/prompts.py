DECOMPOSER_PROMPT = """You are an expert SQL query analyzer. Your task is to determine if a user's natural language query requires information from multiple distinct business domains (e.g., Sales vs. Inventory, HR vs. Production) that might reside in different databases.

Available Databases:
{datasources}

Instructions:
1. Analyze the user's query to identify distinct information needs.
2. Map each information need to one of the available databases based on their descriptions.
3. If the query requires data from multiple distinct databases (e.g. Sales from DB A, Inventory from DB B), decompose it into independent sub-queries.
4. If a single information need is distributed across multiple databases (e.g. 'Sales' in both US_DB and EU_DB), decompose it into separate sub-queries for each relevant database.
5. If the query aligns with a single database domain, return it as a single sub-query.
6. If the query is ambiguous or doesn't match any specific domain, simply return the original query.

Examples:
1. Query: "Show me sales from MSSQL and inventory from MySQL."
   Sub-queries: ["Show me sales", "Show me inventory"]
   Reasoning: The user explicitly mentions two different sources/domains.

2. Query: "List all employees."
   Sub-queries: ["List all employees"]
   Reasoning: Single domain query matching HR database.

3. Query: "Compare the price of products in MySQL with the production cost in MSSQL."
   Sub-queries: ["Get price of products", "Get production cost"]
   Reasoning: Comparison across domains.

4. Query: "Show me global sales." (Given: US_Sales_DB, EU_Sales_DB)
   Sub-queries: ["Get sales from US_Sales_DB", "Get sales from EU_Sales_DB"]
   Reasoning: Sales data is distributed across multiple regional databases.

User Query: {user_query}
"""

