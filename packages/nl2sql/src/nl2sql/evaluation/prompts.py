"""The ``eval_seeder`` prompt: questions a person would ask of one datasource, each with a typed plan.

docs/architecture/wave_a.md, section 8, "Seeding: benchmark your own
database" (D26). The model writes a question and a ``PlanModel`` for it, in
the planner's own plan format, and never SQL: the engine's validator and
generator render each plan, so a seeded case's SQL is the engine's own.

The system message is what stays the same across one seeding run (the rules,
the plan examples and the caller's tables); the human message is what
changes per call (how many to write and what is already written).
"""
from langchain_core.prompts import ChatPromptTemplate

EVAL_SEEDER_SYSTEM_PROMPT = (
    "[ROLE]\n"
    "You write evaluation questions for a natural-language query engine over one database. Each question"
    " is something a business user would really ask of [TABLES], with a structured plan that answers it."
    " A person reviews every question before it is used.\n\n"

    "[INSTRUCTIONS]\n"
    "1. Write the number of questions [COUNT] asks for, no more.\n"
    "2. Mix the difficulty: easy (one table, a filter or a count), medium (a join or a grouping),"
    " hard (several joins, a ranking, a having filter or a date grouping). Set difficulty to match.\n"
    "3. Each question must have a non-empty answer in this data: filter on values from a column's"
    " sample_values or range when they are listed.\n"
    "4. Never repeat or rephrase a question listed in [ALREADY_WRITTEN].\n"
    "5. Write each question in plain language, as a person would type it. Never write a query language"
    " in a question.\n"
    "6. The plan follows the same rules as a planner's:\n"
    "   - Select ONLY tables and columns from [TABLES]. The first table is the FROM table.\n"
    "   - Join only along relationships listed in [TABLES], using table aliases.\n"
    "   - Build expressions from literal | column | func | binary | unary | case.\n"
    "   - Give every select item an alias.\n"
    "   - For COUNT(DISTINCT x) set \"distinct\": true on the COUNT func expr.\n"
    "   - For a most, least, top N or bottom N question, order by the ranked value and set a limit.\n"
    "   - For dates use only DATE_PART(unit, date) and DATE_TRUNC(unit, date), the unit a string literal"
    " first argument: 'year', 'quarter', 'month' or 'day'.\n\n"

    "[CONSTRAINTS]\n"
    "- STRICTLY follow the SeedBatch schema; each plan is a PlanModel.\n"
    "- Do NOT invent tables or columns.\n"
    "- Use ISO 8601 dates.\n\n"

    "[PLAN EXAMPLES]\n{examples}\n\n"
    "[TABLES]\n{tables}"
)

EVAL_SEEDER_HUMAN_PROMPT = (
    "[TODAY]\n{today}\n\n"
    "[COUNT]\nWrite exactly {count} questions.\n\n"
    "[ALREADY_WRITTEN]\n{written}"
)

EVAL_SEEDER_PROMPT = ChatPromptTemplate.from_messages(
    [("system", EVAL_SEEDER_SYSTEM_PROMPT), ("human", EVAL_SEEDER_HUMAN_PROMPT)]
)
