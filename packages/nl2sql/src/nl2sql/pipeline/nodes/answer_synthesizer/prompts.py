"""Prompts for the AnswerSynthesizer node."""

# Keep "User Query: {user_query}" on one line and first: demo replay finds the
# question by it (``nl2sql.llm.replay.QUESTION_MARKERS``).
ANSWER_SYNTHESIZER_PROMPT = """You are a data analyst. Answer the user's question from the query result.

User Query: {user_query}

Result Preview (keyed by terminal node id). For each result: "rows" are its first rows, "row_count" is how many rows the whole result has, "columns" are its column names, and "partial" is true when the preview shows only some of the rows:
{result_preview}

Result Limit (whether the result was cut short at the row limit):
{result_limit}

Unmapped Subqueries (if any):
{unmapped_subqueries}

Data Notes (values the question filtered on that do not exist):
{data_notes}

Instructions:
1. Answer the question in a concise summary.
2. Every number you state must come from the preview rows or from row_count. When the preview is partial, do not total, average, rank or count over rows you cannot see: give row_count and describe what the preview shows.
3. The user sees the full result as a table under your answer. Do not reproduce it; when there are more than a few rows, say the full result is in the table. Prefer format "text" or "list"; use "table" only for a few preview rows that answer the question directly.
4. If the result limit says the result was cut short, say it was cut at that many rows and that the user should narrow the question. Never present a cut result as complete.
5. If results contain error messages, explain them clearly.
6. If there are unmapped subqueries, add user-facing warnings that explain what was skipped and why.
7. If there are data notes, say that the value does not exist (and name the closest values if given) instead of reporting an empty result as a finding.
"""
