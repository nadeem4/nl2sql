import re

from nl2sql.testing.fake_llm import Rule

COUNT_EMPLOYEES_DECOMPOSER = {
    "sub_queries": [{
        "id": "sq1", "datasource_id": "manufacturing_ops",
        "intent": "How many employees are there",
        "metrics": [{"name": "employee_count", "aggregation": "count"}],
        "filters": [], "group_by": [],
        "expected_schema": [{"name": "employee_count", "dtype": "int"}],
    }],
    "combine_groups": [{"group_id": "g1", "operation": "standalone",
                        "inputs": [{"subquery_id": "sq1"}], "join_keys": []}],
    "post_combine_ops": [], "unmapped_subqueries": [],
}

COUNT_EMPLOYEES_PLAN = {
    "query_type": "READ", "distinct": False,
    "tables": [{"name": "employees", "alias": "t1", "ordinal": 0}],
    "joins": [],
    "select_items": [{"ordinal": 0, "alias": "employee_count",
                      "expr": {"kind": "func", "func_name": "COUNT", "is_aggregate": True,
                               "args": [{"kind": "column", "alias": "t1", "column_name": "id"}]}}],
    "group_by": [], "order_by": [],
    "reasoning": "Count rows in employees.",
}


def count_employees_answer(prompt_text: str) -> dict:
    nums = re.findall(r"employee_count[^0-9]*(\d+)", prompt_text)
    n = nums[0] if nums else "?"
    return {"summary": f"There are {n} employees.", "format_type": "text",
            "content": f"There are {n} employees.", "warnings": []}


RULES_COUNT_EMPLOYEES = [
    Rule("DecomposerResponse", COUNT_EMPLOYEES_DECOMPOSER),
    Rule("PlanModel", COUNT_EMPLOYEES_PLAN),
    Rule("AggregatedResponse", count_employees_answer),
    Rule("plain", "Keep the same plan."),
]
