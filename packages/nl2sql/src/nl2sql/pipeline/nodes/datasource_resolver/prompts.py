from langchain_core.prompts import ChatPromptTemplate

# Order matters for prompt caching: the system message (instructions and the
# datasources) is the same for every question, and the question comes last in
# the human message. `{datasources}` is JSON with sorted keys, datasources
# sorted by id.
ANSWERABILITY_SYSTEM_PROMPT = """You check whether a question can be answered from the datasources below, before any SQL is planned. For each datasource you see its description and the tables it holds, not its data.

Rules:
1. List the id of every datasource that could hold data relevant to the question, even partly. A question that needs several datasources lists all of them.
2. Be conservative. When you are unsure, treat the question as answerable and list the datasources that might answer it. Refusing a question that could have been answered is worse than running one that finds nothing.
3. Return an empty list only when the question is clearly about something none of these datasources hold, such as the weather, general knowledge, or small talk.
4. Use only ids from the list below.
5. Give the reason in one short sentence.

Datasources (JSON):
{datasources}"""

# "User Query:" is the marker `nl2sql.llm.replay.extract_question` keys a
# recording on, so a demo recording of this call is kept per question.
ANSWERABILITY_HUMAN_PROMPT = """User Query:
{user_query}"""

ANSWERABILITY_PROMPT = ChatPromptTemplate.from_messages(
    [("system", ANSWERABILITY_SYSTEM_PROMPT), ("human", ANSWERABILITY_HUMAN_PROMPT)]
)
