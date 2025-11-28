
PLANNER_EXAMPLES = """
Examples:

User Query: "Show me the names of users who placed orders in 2023"
Plan:
{
  "reasoning": "Need to join users and orders. Filter orders by year 2023. Select user names.",
  "tables": [{"name": "users", "alias": "u"}, {"name": "orders", "alias": "o"}],
  "joins": [{"left": "users", "right": "orders", "on": ["u.id = o.user_id"], "join_type": "inner"}],
  "filters": [{"column": "o.order_date", "op": "BETWEEN", "value": "2023-01-01 AND 2023-12-31"}],
  "needed_columns": ["users.name", "orders.order_date", "users.id", "orders.user_id"],
  "group_by": [], "aggregates": [], "having": [], "order_by": [], "limit": null
}

User Query: "Count total orders per user"
Plan:
{
  "reasoning": "Group orders by user_id and count them.",
  "tables": [{"name": "orders", "alias": "o"}],
  "joins": [],
  "filters": [],
  "group_by": ["o.user_id"],
  "aggregates": [{"expr": "COUNT(*)", "alias": "total_orders"}],
  "needed_columns": ["orders.user_id"],
  "having": [], "order_by": [], "limit": null
}
"""

PLANNER_PROMPT = (
    "[ROLE]\n"
    "You are a SQL Planner. Your job is to create a structured execution plan for a SQL query based on the user's request and the database schema.\n\n"
    "[INSTRUCTIONS]\n"
    "Follow this algorithm to create the plan:\n"
    "1. Analyze the User Query and Intent to understand the goal.\n"
    "2. Identify the necessary tables from the [SCHEMA].\n"
    "3. Select the required columns. IMPORTANT: List EVERY column used (in SELECT, WHERE, JOIN, GROUP BY) in the 'needed_columns' field.\n"
    "4. Formulate the joins required to connect the tables.\n"
    "5. Construct the final plan JSON.\n\n"
    "[CONSTRAINTS]\n"
    "- Return ONLY a JSON object matching the schema.\n"
    "- Use ONLY tables and columns defined in [SCHEMA]. Do not hallucinate.\n"
    "- If a column is ambiguous, use the most logical one or ask for clarification (if possible).\n\n"
    "[SCHEMA]\n"
    "Allowed tables: {allowed_tables}\n"
    "Allowed columns by table: {allowed_columns}\n"
    "{fk_text}\n"
    "[INPUT]\n"
    "{intent_context}"
    "{examples}\n"
    "User query: \"{user_query}\"\n\n"
    "{format_instructions}"
)
