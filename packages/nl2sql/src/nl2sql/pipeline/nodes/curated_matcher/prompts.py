from langchain_core.prompts import ChatPromptTemplate

# The candidates are shown like tools: an id, what the query returns, and its
# parameters. Never the SQL. `{candidates}` is JSON with sorted keys, in the
# order the search ranked them.
CURATED_MATCHER_SYSTEM_PROMPT = """You decide whether one of the verified queries below answers the user's question. A data team wrote and checked each one; you cannot change what a query does, only pick one and give its parameter values.

Rules:
1. Pick a query only when it answers the whole question as asked: the same measure, the same ranking or grouping, the same filters. A query about something close but different (ranked by another measure, grouped another way, a different entity) does not answer it.
2. When no query answers the whole question, set query_id to "none_of_these". When unsure, choose "none_of_these": the question is then answered another way, which is better than a wrong answer.
3. Use the id exactly as listed (datasource.query).
4. Give a value for each parameter the question states, as text: an int as digits, a date as YYYY-MM-DD, a string exactly as one of its choices when it has them. Leave out a parameter the question does not state; never guess a value the question does not give.
5. Give the reason in one short sentence.

Verified queries (JSON):
{candidates}"""

# "User Query:" is the marker `nl2sql.llm.replay.extract_question` keys a
# recording on, so a demo recording of this call is kept per question.
CURATED_MATCHER_HUMAN_PROMPT = """User Query:
{user_query}"""

CURATED_MATCHER_PROMPT = ChatPromptTemplate.from_messages(
    [("system", CURATED_MATCHER_SYSTEM_PROMPT), ("human", CURATED_MATCHER_HUMAN_PROMPT)]
)
