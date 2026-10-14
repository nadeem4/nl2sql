"""Ask the Chinook demo a question and print what the engine decided.

Run these two commands first:

    pip install nl2sql-engine
    export OPENAI_API_KEY=...          # Windows: set OPENAI_API_KEY=...

`NL2SQL.demo()` opens the three bundled demo databases read-only from the
installed package and keeps its index under `./.nl2sql-demo/`. Indexing needs
no key: the demo embeds locally. Answering the question below does need one,
because `run_query` calls the model several times.
"""

from nl2sql import NL2SQL, UserContext

engine = NL2SQL.demo()
if engine.index_health()["status"] != "ok":
    engine.index_all_datasources()
# The role decides which tables the plan may read; `admin` may read them all.
result = engine.run_query("How many customers are there?", user_context=UserContext(roles=["admin"]))

for sq in result.sub_queries:
    print(sq.sql)
    print([c.name for c in sq.validation if c.passed])
    print(sq.rows.rows[:5] if sq.rows else "plan only")
print(result.final_answer["summary"])
