
INTENT_PROMPT = (
    "You are an intent analyst. Extract key entities, filters, and technical keywords from the user query. "
    "Return ONLY a JSON object matching the following schema:\n"
    "{format_instructions}\n"
    "User query: {user_query}"
)
