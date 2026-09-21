# Demo Data (CLI-first)

The demo is one dataset: [Chinook](https://github.com/lerocha/chinook-database),
a small digital music store. It is vendored in the wheel (MIT, v1.4.5, 1.02 MB)
and copied into your project by the CLI, so nothing is downloaded and nothing is
generated.

## 1. Install the CLI

```bash
# Install from PyPI. Add the `demo` extra if you also want the browser
# playground that `nl2sql demo` serves; `[all]` covers drivers only.
pip install nl2sql-engine
pip install "nl2sql-engine[demo]"

# Or install from source (dev)
pip install -e "packages/nl2sql[all,demo]"
```

## 2. Write the demo project

```bash
nl2sql setup --demo
```

This writes the following, relative to the directory you run the command in:

- `data/chinook.sqlite`
- `configs/datasources.demo.yaml`
- `configs/llm.demo.yaml`
- `configs/policies.demo.json`
- `configs/sample_questions.demo.yaml`
- `configs/secrets.demo.yaml`
- `.env.demo`

Setup then runs schema indexing once, automatically.

For the browser playground instead of the CLI, `nl2sql demo` does the same
scaffolding in `./nl2sql-demo` and serves a page over it. See the
[README](https://github.com/nadeem4/nl2sql#try-it) for its flags.

### The schema

Eleven tables with real declared foreign keys: `Artist`, `Album`, `Track`,
`Genre`, `MediaType`, `Playlist`, `PlaylistTrack`, `Customer`, `Employee`,
`Invoice` and `InvoiceLine`. The foreign keys matter: `LogicalValidatorNode`
checks every join in a plan against a declared relationship, so a dataset
without them cannot answer a join question at all.

### The roles

`configs/policies.demo.json` is generated with three roles, so the RBAC check is
visible without editing anything:

| Role | May read |
| --- | --- |
| `admin` | every table |
| `analyst` | everything except `Employee` |
| `viewer` | the music catalog only - no `Customer`, `Invoice`, `InvoiceLine` or `Employee` |

Ask the `viewer` role about customers and the logical validator refuses the plan
with a `SECURITY_VIOLATION` before any SQL is generated.

### API keys in the demo

`.env.demo` is generated with `EMBEDDING_PROVIDER=local`, so schema chunks are
embedded with the key-free ONNX `all-MiniLM-L6-v2` model bundled with chromadb
instead of the OpenAI embeddings API. The first indexing run downloads roughly
79 MB of model files into a local cache, which can take a few minutes.

This covers the embedding step only. The demo is **not** key-free end to end:

- `nl2sql --env demo run "..."` calls a chat model, so it needs a working LLM key
  (`OPENAI_API_KEY`, or an OpenRouter key with `configs/llm.demo.yaml` pointed at
  `provider: openrouter`).
- Indexing also runs an optional LLM enrichment pass over the schema. Enrichment
  is best-effort: without a usable chat key it is skipped with an `INFO` line
  naming the agent, the provider and the variable to set, and the chunks are
  still indexed - just with no LLM-generated descriptions. An enrichment failure
  never fails indexing.
- Both `nl2sql setup --api-key <key>` and `nl2sql demo --api-key <key>` take the
  key on the command line and write it into the generated env file. The provider
  follows the key's shape: a key beginning `sk-or-` is OpenRouter and is stored
  as `OPENROUTER_API_KEY` with `provider: openrouter`; anything else is OpenAI.
  `.env` and `.env.*` are covered by `.gitignore`, but a key passed on the
  command line is visible in shell history and to `ps`, so the environment
  variable remains the more private route.
- So `nl2sql setup --demo` and `nl2sql --env demo index` complete with no chat
  key at all, and `nl2sql index` exits `0`. It exits `1` if any datasource
  actually fails to index, which is safe to rely on in a script. A missing key
  surfaces when a chat model is first called. Fill in `OPENAI_API_KEY` in
  `.env.demo` before running a query.
- Pointing `configs/llm.demo.yaml` at `provider: ollama` removes the chat key
  requirement, but a local model's ability to satisfy the pipeline's structured
  output is model-dependent - see
  [LLM configuration → Ollama](../configuration/llm.md#ollama).

Because the demo indexes with `local` and the default environment indexes with
`openai`, the two use different vector dimensions. `.env.demo` keeps its own
`VECTOR_STORE=data/vector_store_demo` directory, so they do not collide. If you
change `EMBEDDING_PROVIDER` for an existing store, re-run `nl2sql index` — the
store otherwise raises `EmbeddingDimensionMismatchError`.

## 3. Use the demo from the CLI

```bash
# Run a query against the demo
nl2sql --env demo run "How many customers do we have, by country?"

# Ask as a role that is not allowed the answer
nl2sql --env demo run --role viewer "Who are the top 5 customers by total spend?"

# Plan and validate without touching the database
nl2sql --env demo run --no-exec "Which artist has the most albums?"

# Re-index after regenerating or editing the demo configs
nl2sql --env demo index
```

`--env <name>` loads `.env.<name>`. To point at an exact file instead, use
`--env-file <path>`, which takes precedence over `--env`. The equivalent
environment variables (`ENV` and `ENV_FILE_PATH`) still work.

Note: the demo datasource config uses a relative database path
(`data/chinook.sqlite`), so run the CLI from the directory you ran
`nl2sql setup --demo` in.

## Sample questions

`configs/sample_questions.demo.yaml` is written with twelve questions, the same
ones the playground offers as guided questions:

- How many customers do we have, by country?
- Who are the top 5 customers by total spend?
- Which artist has the most albums?
- What is the total revenue per year?
- Which genre sells the most tracks?
- What is the average invoice total by billing country?
- Which employees support the most customers?
- What is the longest track in each genre?
- Which customers bought jazz tracks but never rock?
- What was the monthly revenue in 2013 for customers in the USA?
- Which playlists contain tracks from more than three genres?
- Who are the top customers by total spend?

Whether a given question is answered correctly depends on the model; see
[Known limitations](https://github.com/nadeem4/nl2sql#known-limitations).

## Regenerating the demo

Re-running `nl2sql setup --demo` overwrites `data/chinook.sqlite` and the
`configs/*.demo.*` files with fresh copies. An existing `.env.demo` is
overwritten too, so a key recorded there is lost - pass `--api-key` again, or
re-add it afterwards.

## There is only one demo dataset

`--lite` and `--docker` chose between a generated manufacturing dataset in
SQLite and the same data in a Postgres/MySQL/MSSQL Compose stack. Both are gone:
the manufacturing DDL declared no foreign keys, so it could not answer a join
question, and it was the only dataset the test suite ran on. The Postgres, MySQL
and MSSQL **adapters** are unaffected - they are product features, configured
like any other datasource. A cross-database demo will return with a dataset
designed for it.
