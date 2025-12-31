PLANNER_EXAMPLES = """
Examples:

User Query: "Show me the names of users who placed orders in 2023"
Entities:
[
  {"entity_id": "E1", "name": "users"},
  {"entity_id": "E2", "name": "orders"}
]

Plan:
{
  "reasoning": "Orders contain transactional data filtered by year. Users provide names. Join orders to users on user_id and select user names for matching orders in 2023.",
  "tables": [
    {"name": "users", "alias": "t1"},
    {"name": "orders", "alias": "t2"}
  ],
  "joins": [
    {
      "left": "users",
      "right": "orders",
      "on": ["t1.id = t2.user_id"],
      "join_type": "inner"
    }
  ],
  "filters": [
    {
      "column": {"expr": "t2.order_date"},
      "op": "BETWEEN",
      "value": "'2023-01-01' AND '2023-12-31'"
    }
  ],
  "select_columns": [
    {"expr": "t1.name", "alias": "name", "is_derived": false}
  ],
  "group_by": [],
  "having": [],
  "order_by": [],
  "limit": null
}

User Query: "Count total orders per user, show only those with more than 5 orders"
Entities:
[
  {"entity_id": "E1", "name": "orders"}
]

Plan:
{
  "reasoning": "Orders are grouped by user_id and counted. Filter groups where total count exceeds 5.",
  "tables": [
    {"name": "orders", "alias": "t1"}
  ],
  "joins": [],
  "filters": [],
  "select_columns": [
    {"expr": "t1.user_id", "alias": "user_id", "is_derived": false},
    {"expr": "COUNT(*)", "alias": "total_orders", "is_derived": true}
  ],
  "group_by": ["t1.user_id"],
  "having": [
    {"expr": "COUNT(*)", "op": ">", "value": 5}
  ],
  "order_by": [],
  "limit": null
}
"""

PLANNER_PROMPT = (
    "[ROLE]\n"
    "You are a SQL Planner.\n"
    "Your job is to create a structured, executable SQL plan based on the User Query and Schema Context.\n\n"

    "[INSTRUCTIONS]\n"
    "Follow this algorithm to create the plan:\n"
    "1. Analyze the [USER_QUERY] to understand the intent and data requirements.\n"
    "2. Identify tables from [SCHEMA] that contain the required data.\n"
    "3. Explain your step-by-step reasoning in the 'reasoning' field.\n"
    "4. Populate the 'tables' list using ONLY tables from [SCHEMA].\n"
    "   - The 'tables' list MUST include every table used, including join tables.\n"
    "   - Use the EXACT aliases provided in [SCHEMA] (e.g., t1, t2).\n"
    "5. Formulate joins correctly between tables using Foreign Key relationships or name conventions.\n"
    "6. Select the required columns.\n"
    "   - 'select_columns' must contain objects of the form:\n"
    "     {{\"expr\": \"...\", \"alias\": \"...\", \"is_derived\": ...}}\n"
    "   - Use pre-aliased column names from schema (e.g., t1.name).\n"
    "   - Set is_derived=true for aggregates or expressions.\n"
    "7. Apply filters, grouping, having, ordering, and limits as required.\n\n"

    "[DATATYPE RULES]\n"
    "- Dates must follow this format: '{date_format}'.\n"
    "- Strings and dates must use single quotes.\n"
    "- Numbers must NOT be quoted unless the column type is string.\n\n"

    "[CONSTRAINTS]\n"
    "- Return ONLY a JSON object matching the expected plan schema.\n"
    "- Use ONLY tables and columns defined in [SCHEMA].\n"
    "- Do NOT hallucinate tables, columns, or joins.\n"
    "- The 'tables' list MUST contain all tables referenced in joins.\n"
    "- All column references MUST use 'expr'.\n"
    "- Use 'alias' ONLY in 'select_columns'.\n"
    "- Derived expressions MUST set is_derived=true.\n\n"

    "[SCHEMA]\n"
    "{schema_context}\n\n"

    "[CONFIG]\n"
    "Date Format: {date_format}\n\n"

    "[EXAMPLES]\n"
    "{examples}\n\n"

    "[FEEDBACK]\n"
    "{feedback}\n\n"

    "[USER_QUERY]\n"
    "{user_query}"
)
