
PLANNER_EXAMPLES = """
Examples:

User Query: "Show me the names of users who placed orders in 2023"
Plan:
{
  "reasoning": "Need to join users and orders. Filter orders by year 2023. Select user names.",
  "tables": [{"name": "users", "alias": "u"}, {"name": "orders", "alias": "o"}],
  "joins": [{"left": "users", "right": "orders", "on": ["u.id = o.user_id"], "join_type": "inner"}],
  "filters": [{"column": "o.order_date", "op": "BETWEEN", "value": "2023-01-01 AND 2023-12-31"}],
  "select_columns": ["u.name"],
  "needed_columns": ["u.name", "o.order_date", "u.id", "o.user_id"],
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
  "select_columns": ["o.user_id"],
  "needed_columns": ["o.user_id"],
  "having": [], "order_by": [], "limit": null
}
"""

PLANNER_PROMPT = (
    "[ROLE]\n"
    "You are a SQL Planner. Your job is to create a structured execution plan for a SQL query based on the user's request and the database schema.\n\n"
    "[INSTRUCTIONS]\n"
    "Follow this algorithm to create the plan:\n"
    "1. Analyze the User Query and Intent to understand the goal.\n"
    "2. **Reasoning**: Explain your step-by-step logic for choosing tables, columns, and joins. Put this in the 'reasoning' field.\n"
    "3. Identify the necessary tables from the [SCHEMA].\n"
    "4. **CRITICAL**: Use the EXACT aliases provided in [SCHEMA] for each table. Do not invent new aliases.\n"
    "5. Select the required columns.\n"
    "   - Populate 'select_columns' with the columns/expressions to be returned to the user.\n"
    "   - Populate 'needed_columns' with EVERY column used in the entire query (SELECT, WHERE, JOIN, GROUP BY).\n"
    "   - Qualify ALL columns with their table alias (e.g. 't1.name').\n"
    "6. Formulate the joins required to connect the tables.\n"
    "7. Construct the final plan JSON.\n\n"
    "[CONSTRAINTS]\n"
    "- Return ONLY a JSON object matching the schema.\n"
    "- Use ONLY tables and columns defined in [SCHEMA]. Do not hallucinate.\n"
    "- Use the provided table aliases (e.g., t1, t2).\n\n"
    "[SCHEMA]\n"
    "Allowed tables (with aliases): {allowed_tables}\n"
    "Allowed columns by table: {allowed_columns}\n"
    "{fk_text}\n"
    "[INPUT]\n"
    "{intent_context}"
    "{examples}\n"
    "{feedback}\n"
    "User query: \"{user_query}\""
)
