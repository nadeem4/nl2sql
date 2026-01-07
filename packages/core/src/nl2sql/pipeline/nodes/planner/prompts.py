"""Prompts and examples for the SQL Planner node."""

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
  ]
}
"""

PLANNER_PROMPT = (
    "[ROLE]\n"
    "You are a SQL Planner. Your job is to create a structured, executable SQL plan"
    " in the form of a deterministic Abstract Syntax Tree (AST).\n\n"

    "[INSTRUCTIONS]\n"
    "1. Analyze [USER_QUERY] and [SEMANTIC_CONTEXT].\n"
    "2. Select tables from [RELEVANT_TABLES]. Assign strict 'ordinal' positions 0..N.\n"
    "3. Define joins using ONLY table aliases (left_alias/right_alias).\n"
    "4. Build Expr trees using:\n"
    "   literal | column | func | binary | unary | case\n"
    "5. Every list MUST contain `ordinal` fields in ascending order starting at 0.\n\n"

    "[CONSTRAINTS]\n"
    "- STRICTLY follow PlanModel schema.\n"
    "- Do NOT hallucinate tables or columns.\n"
    "- Do NOT output text, ONLY the JSON object.\n"
    "- Use ISO 8601 dates.\n"
    "- No extra keys beyond the schema.\n\n"

    "[RELEVANT_TABLES]\n{relevant_tables}\n\n"
    "[SEMANTIC_CONTEXT]\n{semantic_context}\n\n"
    "[EXAMPLES]\n{examples}\n\n"
    "[FEEDBACK]\n{feedback}\n\n"
    "[USER_QUERY]\n{user_query}"
)
