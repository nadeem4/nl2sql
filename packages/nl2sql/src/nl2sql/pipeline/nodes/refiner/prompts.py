from langchain_core.prompts import ChatPromptTemplate

# Cache layout, as in the planner: the system message is the stable part
# (instructions, then the schema), the human message is the per-call part. The
# system/human boundary is the single cache seam.
REFINER_SYSTEM_PROMPT = """You are an expert SQL debugger and schema analyst.
Your goal is to analyze a failed SQL generation attempt and provide actionable, schema-aware feedback to the Planner.

### Instructions
1. Analyze the Errors in the context of the Schema and User Query.
2. If the error is an Execution Error (e.g., syntax error, runtime error), analyze why the SQL failed and suggest a fix in the Plan.
2. If the error is about a missing table or column, check the Schema for the correct name.
   - Example: Error "Column 'revenue' not found", Schema has "total_revenue". -> Suggest "Use 'total_revenue' instead of 'revenue'".
3. If the plan is empty or missing, analyze the User Query and suggest which tables/columns to use.
4. Provide a concise, numbered list of specific fixes. Do not generate SQL. Focus on correcting the Plan.

### Database Schema
{relevant_tables}"""

REFINER_HUMAN_PROMPT = """### Context
User Query: "{user_query}"

### Failed Plan
{failed_plan}

### Errors (Validation or Execution)
{errors}

### Previous Reasoning (Trace)
{reasoning}

### Feedback
"""

REFINER_PROMPT = ChatPromptTemplate.from_messages(
    [("system", REFINER_SYSTEM_PROMPT), ("human", REFINER_HUMAN_PROMPT)]
)
