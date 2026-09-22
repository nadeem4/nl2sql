"""Ask the Chinook demo a question and print what the engine decided.

Run these three commands first, from the directory you want the demo in:

    pip install nl2sql-engine
    nl2sql setup --demo
    export OPENAI_API_KEY=...          # Windows: set OPENAI_API_KEY=...

`nl2sql setup --demo` copies the vendored Chinook database to
`data/chinook.sqlite`, writes the `configs/*.demo.*` files and `.env.demo`, and
indexes the schema. That part needs no key -- `.env.demo` sets
`EMBEDDING_PROVIDER=local`. Answering the question below does need one:
`NL2SQL(env="demo")` loads `.env.demo`, and `run_query` calls the model several
times.

Then run this script from that same directory, because the generated datasource
config uses a relative path.
"""

from nl2sql import NL2SQL, UserContext

engine = NL2SQL(env="demo")
# The role decides which tables the plan may read; `admin` may read them all.
result = engine.run_query("How many customers are there?", user_context=UserContext(roles=["admin"]))

for sq in result.sub_queries:
    print(sq.sql)
    print([c.name for c in sq.validation if c.passed])
    print(sq.rows.rows[:5] if sq.rows else "plan only")
print(result.final_answer["summary"])
