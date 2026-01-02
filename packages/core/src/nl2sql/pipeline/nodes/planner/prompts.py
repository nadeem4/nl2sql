PLANNER_EXAMPLES = """
Examples:

User Query: "Show me the names of users who placed orders in 2023"
Semantic Context:
{
  "canonical_query": "List names of users with orders in 2023",
  "keywords": ["users", "orders"]
}

Plan:
{
  "reasoning": "Filter orders by year 2023. Join users. Select user name.",
  "tables": [
    {"name": "users", "alias": "t1", "ordinal": 0},
    {"name": "orders", "alias": "t2", "ordinal": 1}
  ],
  "joins": [
    {
      "left_alias": "t1",
      "right_alias": "t2",
      "join_type": "inner",
      "ordinal": 0,
      "condition": {
        "kind": "binary",
        "op": "=",
        "left": {"kind": "column", "alias": "t1", "column_name": "id"},
        "right": {"kind": "column", "alias": "t2", "column_name": "user_id"}
      }
    }
  ],
  "where": {
    "kind": "binary",
    "op": "AND",
    "left": {
      "kind": "binary",
      "op": ">=",
      "left": {"kind": "column", "alias": "t2", "column_name": "order_date"},
      "right": {"kind": "literal", "value": "2023-01-01"}
    },
    "right": {
      "kind": "binary",
      "op": "<=",
      "left": {"kind": "column", "alias": "t2", "column_name": "order_date"},
      "right": {"kind": "literal", "value": "2023-12-31"}
    }
  },
  "select_items": [
    {
      "ordinal": 0,
      "expr": {"kind": "column", "alias": "t1", "column_name": "name"},
      "alias": "user_name"
    }
  ],
  "group_by": [],
  "having": null,
  "order_by": [],
  "limit": null
}

User Query: "Count total orders per user, show only those with more than 5 orders"

Plan:
{
  "reasoning": "Group orders by user_id and count. Filter groups > 5.",
  "tables": [
    {"name": "orders", "alias": "t1", "ordinal": 0}
  ],
  "joins": [],
  "select_items": [
    {
      "ordinal": 0,
      "expr": {"kind": "column", "alias": "t1", "column_name": "user_id"},
      "alias": "user_id"
    },
    {
      "ordinal": 1,
      "expr": {"kind": "func", "func_name": "COUNT", "args": [{"kind": "column", "alias": "t1", "column_name": "id"}]},
      "alias": "total_orders"
    }
  ],
  "where": null,
  "group_by": [
    {
      "ordinal": 0,
      "expr": {"kind": "column", "alias": "t1", "column_name": "user_id"}
    }
  ],
  "having": {
    "kind": "binary",
    "op": ">",
    "left": {"kind": "func", "func_name": "COUNT", "args": [{"kind": "column", "alias": "t1", "column_name": "id"}]},
    "right": {"kind": "literal", "value": 5}
  },
  "order_by": []
}
"""

PLANNER_PROMPT = (
    "[ROLE]\n"
    "You are a SQL Planner. Your job is to create a structured, executable SQL plan in the form of a Abstract Syntax Tree (AST).\n\n"

    "[INSTRUCTIONS]\n"
    "1. Analyze [USER_QUERY] and [SEMANTIC_CONTEXT].\n"
    "2. Select tables from [RELEVANT_TABLES]. Assign them strict 'ordinal' positions (0, 1, 2...).\n"
    "3. Define joins using exact table ALIASES (not names) for `left_alias` and `right_alias`.\n"
    "4. Construct the query logic using Recursive 'Expr' objects:\n"
    "   - kind='column': {{\"kind\": \"column\", \"alias\": \"t1\", \"column_name\": \"col\"}}\n"
    "   - kind='literal': {{\"kind\": \"literal\", \"value\": 100}}\n"
    "   - kind='binary': {{\"kind\": \"binary\", \"op\": \">\", \"left\": {{...}}, \"right\": {{...}}}}\n"
    "   - kind='func': {{\"kind\": \"func\", \"func_name\": \"COUNT\", \"args\": [{{...}}]}}\n"
    "5. Populate 'select_items', 'group_by', 'order_by' lists with explicit 'ordinal' integers to guarantee output order.\n\n"

    "[CONSTRAINTS]\n"
    "- Return ONLY the JSON object matching the PlanModel schema.\n"
    "- Do NOT hallucinate tables. Use [RELEVANT_TABLES] only.\n"
    "- All lists MUST have 'ordinal' fields starting at 0.\n"
    "- Use ISO 8601 for dates in 'literal' values.\n\n"

    "[RELEVANT_TABLES]\n"
    "{relevant_tables}\n\n"

    "[SEMANTIC_CONTEXT]\n"
    "{semantic_context}\n\n"

    "[CONFIG]\n"
    "Date Format: {date_format}\n\n"

    "[EXAMPLES]\n"
    "{examples}\n\n"

    "[FEEDBACK]\n"
    "{feedback}\n\n"

    "[USER_QUERY]\n"
    "{user_query}"
)
