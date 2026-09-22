"""Prompts and examples for the SQL Planner node."""

from langchain_core.prompts import ChatPromptTemplate

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

User Query: "Total revenue by region last quarter"
Expected Schema:
[
  {"name": "region", "dtype": "string"},
  {"name": "total_revenue", "dtype": "float"}
]

Plan:
{
  "reasoning": "Group by region and sum revenue.",
  "tables": [
    {"name": "orders", "alias": "t1", "ordinal": 0}
  ],
  "joins": [],
  "where": {
    "kind": "binary",
    "op": "=",
    "left": {"kind": "column", "alias": "t1", "column_name": "quarter"},
    "right": {"kind": "literal", "value": "last_quarter"}
  },
  "select_items": [
    {
      "ordinal": 0,
      "expr": {"kind": "column", "alias": "t1", "column_name": "region"},
      "alias": "region"
    },
    {
      "ordinal": 1,
      "expr": {"kind": "func", "func_name": "SUM", "args": [{"kind": "column", "alias": "t1", "column_name": "revenue"}], "is_aggregate": true},
      "alias": "total_revenue"
    }
  ],
  "group_by": [
    {
      "ordinal": 0,
      "expr": {"kind": "column", "alias": "t1", "column_name": "region"}
    }
  ]
}

User Query: "How many different customers placed orders?"

Plan:
{
  "reasoning": "Count distinct customer ids on orders.",
  "tables": [
    {"name": "orders", "alias": "t1", "ordinal": 0}
  ],
  "joins": [],
  "select_items": [
    {
      "ordinal": 0,
      "expr": {"kind": "func", "func_name": "COUNT", "args": [{"kind": "column", "alias": "t1", "column_name": "user_id"}], "is_aggregate": true, "distinct": true},
      "alias": "customer_count"
    }
  ]
}
"""

# Cache layout. The system message is everything that is the same for every
# question on a datasource and role: instructions, examples, then the schema.
# The human message is everything that changes per call. Providers cache a
# stable prompt prefix, so the system/human boundary is the single cache seam:
# keep per-question content out of the system message.
PLANNER_SYSTEM_PROMPT = (
    "[ROLE]\n"
    "You are a SQL Planner. Your job is to create a structured, executable SQL plan"
    " in the form of a deterministic Abstract Syntax Tree (AST).\n\n"

    "[INSTRUCTIONS]\n"
    "1. Analyze [USER_QUERY] and [SEMANTIC_CONTEXT].\n"
    "2. Select ONLY tables from [RELEVANT_TABLES]. Assign strict 'ordinal' positions 0..N.\n"
    "3. When joining, use relationships listed in [RELEVANT_TABLES]. If no relationship exists, do not join.\n"
    "4. Define joins using ONLY table aliases (left_alias/right_alias).\n"
    "5. Build Expr trees using:\n"
    "   literal | column | func | binary | unary | case\n"
    "6. Every list MUST contain `ordinal` fields in ascending order starting at 0.\n"
    "7. Order lists to match ordinals (0..N) exactly.\n"
    "8. For literal values on '=' or 'IN', choose values from a column's sample_values if listed.\n"
    "9. If no exact match is available, fall back to LIKE but keep the pattern derived from sample_values.\n"
    "10. For COUNT(DISTINCT x), set \"distinct\": true on the COUNT func expr; for SELECT DISTINCT,"
    " set the plan's \"distinct\": true. DISTINCT is never a func_name.\n\n"

    "[OUTPUT CONTRACT]\n"
    "- If [EXPECTED_SCHEMA] is provided and non-empty:\n"
    "  - select_items length MUST equal expected_schema length.\n"
    "  - select_items aliases MUST match expected_schema names in the same order.\n"
    "- All table/column references MUST come from [RELEVANT_TABLES].\n"
    "- Joins MUST use relationships provided in [RELEVANT_TABLES].\n"
    "- The [EXAMPLES] are illustrative; always follow [EXPECTED_SCHEMA] when provided.\n\n"

    "[CONSTRAINTS]\n"
    "- STRICTLY follow PlanModel schema.\n"
    "- Do NOT hallucinate tables or columns.\n"
    "- Do NOT output text, ONLY the JSON object.\n"
    "- Use ISO 8601 dates.\n"
    "- No extra keys beyond the schema.\n"
    "{dialect_notes}\n"

    "[EXAMPLES]\n{examples}\n\n"
    "[RELEVANT_TABLES]\n{relevant_tables}"
)

PLANNER_HUMAN_PROMPT = (
    "[EXPECTED_SCHEMA]\n{expected_schema}\n\n"
    "[SEMANTIC_CONTEXT]\n{semantic_context}\n\n"
    "[FEEDBACK]\n{feedback}\n\n"
    "[USER_QUERY]\n{user_query}"
)

# What the planner is told about the database it plans for. Function names in a
# plan are written as the target database spells them, so the planner needs the
# dialect; SQLite gets its date functions spelled out because it has none of
# the usual ones.
_SQLITE_NOTES = (
    "   SQLite stores dates as text and has no DATE_TRUNC, YEAR, MONTH or EXTRACT."
    " Group or label by a period with STRFTIME: STRFTIME('%Y', col) for a year,"
    " STRFTIME('%Y-%m', col) for a month, STRFTIME('%Y-%m-%d', col) for a day.\n"
)


def dialect_notes(dialect: str | None) -> str:
    """The [CONSTRAINTS] lines naming the target SQL dialect, empty when it is unknown."""
    if not dialect:
        return ""
    notes = f"- Target SQL dialect: {dialect}. Use only functions it has.\n"
    return notes + (_SQLITE_NOTES if dialect == "sqlite" else "")


PLANNER_PROMPT = ChatPromptTemplate.from_messages(
    [("system", PLANNER_SYSTEM_PROMPT), ("human", PLANNER_HUMAN_PROMPT)]
).partial(dialect_notes="")
