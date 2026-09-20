"""Ask the generated SQLite demo a question and print what the engine decided.

Run these three commands first, from the directory you want the demo in:

    pip install nl2sql-engine
    nl2sql setup --demo --lite
    export OPENAI_API_KEY=...          # Windows: set OPENAI_API_KEY=...

`nl2sql setup --demo --lite` writes `data/demo_lite/*.db`, the `configs/*.demo.*`
files and `.env.demo`, and indexes the schemas. That part needs no key --
`.env.demo` sets `EMBEDDING_PROVIDER=local`. Answering the question below does
need one: `NL2SQL(env="demo")` loads `.env.demo`, and `run_query` calls the
model several times.

Then run this script from that same directory, because the generated datasource
config uses relative paths.
"""

from nl2sql import NL2SQL

engine = NL2SQL(env="demo")
result = engine.run_query("How many employees are there?")

for sq in result.sub_queries:
    print(sq.sql)
    print([c.name for c in sq.validation if c.passed])
    print(sq.rows.rows[:5] if sq.rows else "plan only")
print(result.final_answer["summary"])
