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
- `nl2sql-demo.json`, a small stamp recording which engine version wrote the
  folder (see [Old demo folders](#old-demo-folders))

Setup then runs schema indexing once, automatically, and exits `1` if it fails.

For the browser playground instead of the CLI, `nl2sql demo` does the same
scaffolding in `./nl2sql-demo` and serves a page over it. See the
[README](https://github.com/nadeem4/nl2sql#try-it) for its flags, and
[the settings panel](#the-settings-panel) for entering a key and choosing
models from the page.

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
with a `SECURITY_VIOLATION` before any SQL is generated. The planner saw the
`Customer` and `Invoice` tables' columns but none of their sample values or
statistics. `.env.demo` sets `RBAC_REFUSAL_NAMES_TABLES=true`, so the demo's
refusal names the role and table; outside the demo the message is the generic
"You do not have permission to see the data this question requires." See
[Security Model](../security/model.md#strict-refusal).

### API keys in the demo

`.env.demo` is generated with `EMBEDDING_PROVIDER=local`, so schema chunks are
embedded with the key-free ONNX `all-MiniLM-L6-v2` model bundled with chromadb
instead of the OpenAI embeddings API. The first indexing run downloads roughly
79 MB of model files into a local cache, which can take a few minutes.

This covers the embedding step only. The demo is **not** key-free end to end:

- `nl2sql --env demo run "..."` calls a chat model, so it needs a working LLM key
  (`OPENAI_API_KEY`, or an OpenRouter key with `configs/llm.demo.yaml` pointed at
  `provider: openrouter`).
- The demo's model is `gpt-5.4` with `temperature: 0.0`. It replaced `gpt-4o`,
  whose 30,000 tokens-per-minute limit on the owner's account was below one
  question with a single retry; `gpt-5.4` had 500,000 and accepts
  `temperature: 0`. To use a model that rejects it (`gpt-5.5`, `gpt-5-mini`),
  set `temperature: null` in `configs/llm.demo.yaml` - see
  [LLM configuration → Temperature](../configuration/llm.md#temperature). With an
  OpenRouter key or Ollama, change `model` to one that provider serves: live
  mode switches the provider, not the model name.
- `nl2sql index` also runs an optional LLM enrichment pass over the schema.
  Enrichment is best-effort: without a usable chat key it is skipped with an
  `INFO` line naming the agent, the provider and the variable to set, and the
  chunks are still indexed - just with no LLM-generated descriptions. An
  enrichment failure never fails indexing. The demo's own indexing (the first
  run, the startup repair and the playground's Rebuild) leaves enrichment
  **off**, because it spends tokens on your key; the playground offers it as an
  explicit checkbox. When it is on, the demo's indexing uses a key exported in
  your shell, or one saved in `.env.demo`; the file's empty `OPENAI_API_KEY=`
  placeholder no longer blanks an exported key while indexing runs.
- Both `nl2sql setup --api-key <key>` and `nl2sql demo --api-key <key>` take the
  key on the command line and write it into the generated env file. The provider
  follows the key's shape: a key beginning `sk-or-` is OpenRouter and is stored
  as `OPENROUTER_API_KEY` with `provider: openrouter`; a key beginning `sk-ant-`
  is Anthropic and is stored as `ANTHROPIC_API_KEY` with `provider: anthropic`,
  model `claude-opus-5` and `temperature: null` (install
  `nl2sql-engine[anthropic]`); anything else is OpenAI.
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
change `EMBEDDING_PROVIDER` for an existing store, run `nl2sql index --full`:
every datasource in a collection must share one embedding model, so the store
otherwise raises `EmbeddingDimensionMismatchError` (or
`EmbeddingModelMismatchError` for a different model of the same size).

### Index health and repair

The playground's schema panel reads the schema snapshot; the resolver, which
picks the database for every question when more than one is registered, reads
the separate vector index (with only Chinook registered it skips the search, but
the schema retriever still reads the index). The two
can disagree: a folder once had its snapshot intact and 0 entries in its index,
so the page looked fine while every question failed with
`SCHEMA_RETRIEVAL_FAILED`. Three things now guard against that:

- **Startup repair.** Every `nl2sql demo` start checks what the index
  *contains*: it rebuilds when the index is missing or empty, or was built from
  an older schema version than the latest snapshot. A failed rebuild is
  reported (the playground still starts, and offers Rebuild) rather than
  ignored.
- **Rebuilds never leave an empty index.** A datasource's new entries are
  written beside its current ones and switched in only when all are written;
  a failure keeps the previous entries answering questions. See
  [Indexing](../architecture/indexing.md#rebuilding-without-an-empty-index).
- **Actionable errors.** With an empty index the resolver says
  "The vector index is empty ... Re-index with `nl2sql index`", and
  `nl2sql --env demo doctor` reports the index under **Index**: entries by type,
  whether each datasource's schema version matches the latest snapshot, and
  when it was built.

### The search index panel

The playground's left rail opens with **Search index**: entries by type
(datasource, tables, columns, relationships), the schema version they were
built from, and when. When the index is empty, missing or out of date, a
warning under the top bar says what that means for a question, and the panel's
**Rebuild the index** button becomes the rail's one filled button. **Rebuild**
is also always available on demand.

- Rebuild re-reads the schema from the database into a new snapshot and
  rebuilds the Chinook entries beside the current ones; questions keep using
  the current entries until the new ones are complete, and questions in flight
  finish before the switch.
- It shows each step as it runs. The first run downloads the 79 MB embedding
  model, so it can take a few minutes.
- **Write descriptions with the LLM** is off by default because it spends
  tokens on your key; it is unavailable in replay mode.
- It has the settings panel's guardrails: local only unless `--allow-settings`,
  and only from the playground page itself.

### Old demo folders

Demo folders are not upgraded in place: a folder written by an older engine
keeps that engine's defaults (for example an older default model, or no
`TRACE_MODE` line). New folders get a `nl2sql-demo.json` stamp; when `nl2sql demo`
starts in a folder stamped by an older engine, or in one with no stamp at all,
it warns and suggests a fresh `--dir`. The playground's index panel repeats the
warning.

### The settings panel

The playground that `nl2sql demo` serves has a **Settings** button at the top
right. It edits the same two files the CLI reads, and nothing else: there is no
second settings store, and the browser keeps nothing but UI conveniences.

- **API key.** Paste a key and press **Save key**. The provider follows the
  key's shape by the same rule as `--api-key` (`sk-ant-` is Anthropic, `sk-or-`
  is OpenRouter, anything else OpenAI). The key is written to the demo project's `.env.demo` and the
  running demo switches from replay to live **without a restart**: questions
  already running finish on the model client they started with, then the
  engine's LLM clients are rebuilt from `configs/llm.demo.yaml`. The key is
  write-only: no response, log line, trace or error carries it, only a masked
  form such as `sk-...4f2a`. On a later start the precedence above still holds,
  so `--api-key` or a key exported in your shell wins over the saved one.
- **A model for each LLM step.** One selector each for the answerability check
  (`datasourceresolver`), question splitter (`decomposer`), query planner (`astplanner`), plan repair (`refiner`) and
  answer writer (`answersynthesizer`), each with a "Default" option that uses
  the `default` agent. A choice is written to `configs/llm.demo.yaml` under
  `agents:`, with the default's provider, endpoint and key reference, and takes
  effect on the next question. The list is short on purpose. On 2026-09-20 each
  model was sent the engine's exact parameters (`temperature=0`, `seed=42`,
  strict `json_schema`) and worked with them, two of them only once the
  temperature was left out. That says the model accepts the engine's calls, not
  how well it plans, which is the evaluation's question:

    | Model | Written with |
    | --- | --- |
    | `gpt-5.4` (the default), `gpt-5.4-mini`, `gpt-4.1`, `gpt-4.1-mini`, `gpt-4o` | `temperature: 0.0` |
    | `gpt-5.5`, `gpt-5-mini` | `temperature: null`: they reject temperature 0, so a step on one runs at the model's default temperature and its answers vary more from run to run |

    With an Anthropic key the list is Claude's. Those entries were not probed
    on a real account; their temperatures follow Anthropic's documented rules:

    | Model | Written with |
    | --- | --- |
    | `claude-opus-5` (the default), `claude-sonnet-5` | `temperature: null`: both reject any temperature |
    | `claude-haiku-4-5` | `temperature: 0.0` |

    The list lives in one place, `VERIFIED_MODELS` in
    `nl2sql/cli/common/api_key.py`. It covers OpenAI and Anthropic for now:
    with an OpenRouter key or Ollama the panel shows the configured model and
    offers no list.
- **Local only by default.** Settings work only when the playground is bound to
  a loopback address (`127.0.0.1`, `localhost`, `::1`). On `0.0.0.0` or any
  other address the panel says why it is off and the settings routes answer
  `403`, because the playground has no login: a stranger who can reach it could
  swap in their own key, or spend on yours. `nl2sql demo --allow-settings` turns
  it on anyway, for a network you trust. Changes are also refused from another
  site's page (a foreign `Origin`), through a hostname that is not a loopback
  name, or in anything but JSON.

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

# Re-index one datasource only; the others' entries are left untouched
nl2sql --env demo index --datasource chinook

# After changing the embedding model: rebuild every datasource, then switch
nl2sql --env demo index --full

# Check the index: entries by type, schema version, when built
nl2sql --env demo doctor
```

The demo's `.env.demo` sets `TRACE_MODE=always`, so every run also writes a
trace to `traces/` and prints its path. Inspect one with
`nl2sql --env demo trace show <file>`, or re-run it on the recorded model
answers with `nl2sql --env demo trace replay <file>`. See
[Debugging a Run](../observability/debugging.md).

Asking a question a second time reuses the first run's validated plan from the
plan cache (in `data/schema_store.db`), so the SQL and rows are the same and the
planner is not called; the playground's Debug view says "Plan from the plan
cache". The plan is still validated for the role you ask as. To start fresh:

```bash
nl2sql --env demo cache clear
```

`nl2sql demo --record` turns the cache off while recording, so every planner
answer is captured. See
[Determinism → The plan cache](../architecture/determinism.md#the-plan-cache-determinism-from-the-architecture).

`--env <name>` loads `.env.<name>`. To point at an exact file instead, use
`--env-file <path>`, which takes precedence over `--env`. Either flag also
loads the file's variables into the process environment, so `doctor` and `run`
find a key kept only in `.env.demo`; a variable already exported in your shell
wins over the file. The equivalent environment variables (`ENV` and
`ENV_FILE_PATH`) still work, but setting them yourself only feeds the settings
object; use the flags when the file holds your key.

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

They are also indexed: the `schema.datasource` entry the resolver matches every
question against carries the datasource's configured description and these
questions (`.env.demo` points `SAMPLE_QUESTIONS` at the file). Folders
generated before this fix wrote the setting as `ROUTING_EXAMPLES`, which nothing
read; the engine now accepts that name too, so re-index such a folder
(`nl2sql --env demo index`) to pick the questions up.

Whether a given question is answered correctly depends on the model; see
[Known limitations](https://github.com/nadeem4/nl2sql#known-limitations).

## Regenerating the demo

Re-running `nl2sql setup --demo` overwrites `data/chinook.sqlite` and the
`configs/*.demo.*` files with fresh copies. An existing `.env.demo` is
overwritten too, so a key recorded there is lost - pass `--api-key` again, save it in the playground's Settings panel, or
re-add it afterwards.

## There is only one demo dataset

`--lite` and `--docker` chose between a generated manufacturing dataset in
SQLite and the same data in a Postgres/MySQL/MSSQL Compose stack. Both are gone:
the manufacturing DDL declared no foreign keys, so it could not answer a join
question, and it was the only dataset the test suite ran on. The Postgres, MySQL
and MSSQL **adapters** are unaffected - they are product features, configured
like any other datasource. A cross-database demo will return with a dataset
designed for it.
