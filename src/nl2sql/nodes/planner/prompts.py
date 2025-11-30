
PLANNER_EXAMPLES = """
Examples:

User Query: "Show me the names of users who placed orders in 2023"
Plan:
{
  "reasoning": "Need to join users and orders. Filter orders by year 2023. Select user names.",
  "tables": [{"name": "users", "alias": "u"}, {"name": "orders", "alias": "o"}],
  "joins": [{"left": "users", "right": "orders", "on": ["u.id = o.user_id"], "join_type": "inner"}],
  "filters": [{"column": {"alias": "o", "name": "order_date"}, "op": "BETWEEN", "value": "2023-01-01 AND 2023-12-31"}],
  "select_columns": [{"alias": "u", "name": "name"}],
  "group_by": [], "aggregates": [], "having": [], "order_by": [], "limit": null
}

User Query: "Count total orders per user, show only those with more than 5 orders"
Plan:
{
  "reasoning": "Group orders by user_id and count them. Filter groups where count > 5.",
  "tables": [{"name": "orders", "alias": "o"}],
  "joins": [],
  "filters": [],
  "group_by": [{"alias": "o", "name": "user_id"}],
  "aggregates": [{"expr": "COUNT(*)", "alias": "total_orders"}],
  "select_columns": [{"alias": "o", "name": "user_id"}],
  "having": [{"expr": "COUNT(*)", "op": ">", "value": 5}],
  "order_by": [], "limit": null
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
    "   - Populate 'select_columns' with the columns to be returned to the user.\n"
    "   - **IMPORTANT**: Columns must be objects: `{{\"alias\": \"...\", \"name\": \"...\"}}`.\n"
    "   - Use the table alias defined in 'tables'.\n"
    "6. Formulate the joins required to connect the tables.\n"
    "7. Construct the final plan JSON.\n\n"
    "[CONSTRAINTS]\n"
    "- Return ONLY a JSON object matching the schema.\n"
    "- Use ONLY tables and columns defined in [SCHEMA]. Do not hallucinate.\n"
    "- Use the provided table aliases (e.g., t1, t2).\n"
    "- All column references (in select, filters, group_by, order_by) MUST be objects with 'alias' and 'name'.\n\n"
    "[SCHEMA]\n"
    "{schema_context}\n"
    "[INPUT]\n"
    "{intent_context}"
    "{examples}\n"
    "{feedback}\n"
    "User query: \"{user_query}\""
)
