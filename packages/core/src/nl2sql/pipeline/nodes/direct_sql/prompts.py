"""Prompts and examples for the Direct SQL generation node."""

DIRECT_SQL_EXAMPLES = """
[EXAMPLE 1]
User Query: "List active machines"
Schema Snippet:
{
  "name": "machines",
  "alias": "m",
  "columns": [{"name": "id"}, {"name": "status"}]
}
Reasoning: The user wants active machines. The 'machines' table has a defined alias 'm'. I will filter by status 'Active'.
SQL: SELECT m.id, m.name FROM machines m WHERE m.status = 'Active'

[EXAMPLE 2]
User Query: "Show orders for customer 'Acme'"
Schema Snippet: 
[
  {"name": "customers", "alias": "c", "columns": [{"name": "id"}, {"name": "name"}]},
  {"name": "orders", "alias": "o", "columns": [{"name": "id"}, {"name": "customer_id"}]}
]
Constraints: customers.id (PK) <- orders.customer_id (FK)
Reasoning: Need to join customers and orders. Schema provides aliases 'c' and 'o'. Join condition is c.id = o.customer_id. Filter by c.name = 'Acme'.
SQL: SELECT o.id, o.order_date, o.total_amount FROM orders o JOIN customers c ON o.customer_id = c.id WHERE c.name = 'Acme'
"""

DIRECT_SQL_PROMPT = """
[ROLE]
You are an expert SQL developer. Your goal is to write a valid SQL query to answer the user's question, outputting the result as a strictly formatted JSON object.

Target Database Dialect: {dialect}

[RELEVANT TABLES]
(The following schemas are in JSON format. Use 'alias' field if present, and 'constraints' for Keys.)
{relevant_tables}

[EXAMPLES]
{examples}

[INSTRUCTIONS]
1. **Analyze Schema**: Read the provided JSON schemas. Identify tables matching the user's intent. Ignore irrelevant tables.
2. **Identify Joins**: Use the 'constraints' fields to find foreign key relationships.
3. **Handle Aliases**:
    - **Check Schema First**: If the Table JSON has an `alias` field (e.g. "alias": "cust"), you **MUST** use it.
    - **Fallback**: If no alias is provided, generate a short, unique one (e.g. `t1` or `emp`).
    - **Mandatory Qualification**: Qualify **EVERY** column with its alias (e.g. `cust.id`, `t1.name`) to prevent ambiguity.
4. **Construct Query**: Write the SQL for the specified dialect.
5. **Reasoning**: Briefly explain your table selection and alias choice.

[CONSTRAINTS]
- Limit results to 100 rows (`TOP 100` or `LIMIT 100`).
- Do not hallucinate columns not in the schema.
- Return valid JSON matching the schema.

[USER QUERY]
{user_query}
"""
