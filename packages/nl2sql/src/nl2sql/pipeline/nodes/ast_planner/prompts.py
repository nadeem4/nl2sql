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
    {"name": "users", "alias": "t1"},
    {"name": "orders", "alias": "t2"}
  ],
  "joins": [
    {
      "left_alias": "t1",
      "right_alias": "t2",
      "join_type": "inner",
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
    {"name": "orders", "alias": "t1"}
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
      "expr": {"kind": "column", "alias": "t1", "column_name": "region"},
      "alias": "region"
    },
    {
      "expr": {"kind": "func", "func_name": "SUM", "args": [{"kind": "column", "alias": "t1", "column_name": "revenue"}], "is_aggregate": true},
      "alias": "total_revenue"
    }
  ],
  "group_by": [
    {
      "expr": {"kind": "column", "alias": "t1", "column_name": "region"}
    }
  ]
}

User Query: "Which region has the highest total revenue?"
Semantic Context:
{"group_by": [{"attribute": "region"}], "limit": 1, "metrics": [{"aggregation": "sum", "name": "total_revenue"}], "order_by": [{"attribute": "total_revenue", "direction": "desc"}]}

Plan:
{
  "reasoning": "Sum revenue per region, highest first, keep one row.",
  "tables": [
    {"name": "orders", "alias": "t1"}
  ],
  "joins": [],
  "select_items": [
    {"expr": {"kind": "column", "alias": "t1", "column_name": "region"}, "alias": "region"},
    {
      "expr": {"kind": "func", "func_name": "SUM", "args": [{"kind": "column", "alias": "t1", "column_name": "revenue"}], "is_aggregate": true},
      "alias": "total_revenue"
    }
  ],
  "group_by": [
    {"expr": {"kind": "column", "alias": "t1", "column_name": "region"}}
  ],
  "order_by": [
    {
      "direction": "desc",
      "expr": {"kind": "func", "func_name": "SUM", "args": [{"kind": "column", "alias": "t1", "column_name": "revenue"}], "is_aggregate": true}
    }
  ],
  "limit": 1
}

User Query: "How many different customers placed orders?"

Plan:
{
  "reasoning": "Count distinct customer ids on orders.",
  "tables": [
    {"name": "orders", "alias": "t1"}
  ],
  "joins": [],
  "select_items": [
    {
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
    "2. Select ONLY tables from [RELEVANT_TABLES]. The first one is the FROM table.\n"
    "3. When joining, use relationships listed in [RELEVANT_TABLES]. If no relationship exists, do not join.\n"
    "4. Define joins using ONLY table aliases (left_alias/right_alias).\n"
    "5. Build Expr trees using:\n"
    "   literal | column | func | binary | unary | case\n"
    "   Join strings (a full name, a label) with the binary op \"||\", never \"+\".\n"
    "6. Every list is read in the order you write it. There is no position field.\n"
    "7. For literal values on '=' or 'IN', choose values from a column's sample_values if listed.\n"
    "8. If no exact match is available, fall back to LIKE but keep the pattern derived from sample_values.\n"
    "9. For COUNT(DISTINCT x), set \"distinct\": true on the COUNT func expr; for SELECT DISTINCT,"
    " set the plan's \"distinct\": true. DISTINCT is never a func_name.\n"
    "10. [SEMANTIC_CONTEXT] is the query's structured intent. Apply every part of it: its filters"
    " (on an aggregated metric, as having), its order_by as the plan's order_by, and its limit as"
    " the plan's limit.\n"
    "11. A question for the most, least, highest, lowest, top N or bottom N rows needs an order_by on"
    " the ranked value (desc for most/highest/top) and a limit (1 for a single answer, N for top N).\n"
    "12. For any date grouping or filtering, use only the two portable date functions:\n"
    "   DATE_PART(unit, date) returns an integer (DATE_PART('year', d) = 2011);\n"
    "   DATE_TRUNC(unit, date) returns the period's first day as 'YYYY-MM-DD'.\n"
    "   The unit is a string literal first argument: 'year', 'quarter', 'month' or 'day'.\n"
    "   Never use any other date or time function.\n\n"

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
    "- No extra keys beyond the schema.\n\n"

    "[EXAMPLES]\n{examples}\n\n"
    "[RELEVANT_TABLES]\n{relevant_tables}"
)

PLANNER_HUMAN_PROMPT = (
    "[EXPECTED_SCHEMA]\n{expected_schema}\n\n"
    "[SEMANTIC_CONTEXT]\n{semantic_context}\n\n"
    "[FEEDBACK]\n{feedback}\n\n"
    "[USER_QUERY]\n{user_query}"
)

PLANNER_PROMPT = ChatPromptTemplate.from_messages(
    [("system", PLANNER_SYSTEM_PROMPT), ("human", PLANNER_HUMAN_PROMPT)]
)
