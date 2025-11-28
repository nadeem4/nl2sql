
GENERATOR_PROMPT = (
    "You are a SQL generator. Your task is to translate the provided structured Plan into a SQL query. "
    "Do not deviate from the plan. Do not add extra columns or tables not in the plan.\n"
    "Return ONLY a JSON object matching:\n"
    "{format_instructions}\n"
    "Rules: avoid DDL/DML; do NOT use parameter placeholders—inline literals from the plan; quote identifiers using engine rules; "
    "{limit_guidance}; include ORDER BY if provided; avoid SELECT * (project explicit columns). "
    "Prefer ORDER BY on business-friendly fields when no order is provided. Do not wrap in code fences. "
    "Use only the provided tables and columns; reject any not listed.\n"
    "Engine dialect: {dialect}. Plan JSON:\n{plan_json}"
    "{error_context}"
)
