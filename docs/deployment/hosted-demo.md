# Hosted demo

`nl2sql demo --hosted` serves the playground as a **public demo of the sample
databases**, where the server holds no API key and every visitor brings their
own.

It is a third state, not a loosened `--allow-settings`. Locally, `nl2sql demo`
saves your key to the demo project's `.env.demo` and spends it on your
questions; that is right on your own machine and wrong on a shared address.
Hosted mode removes the key from the server entirely instead of relaxing the
gate that protects it.

| | local (`nl2sql demo`) | hosted (`nl2sql demo --hosted`) |
| --- | --- | --- |
| the API key | saved to `.env.demo`, used by the process | held by the browser tab, sent per request, used in memory, dropped |
| Settings | writes `.env.demo` and `configs/llm.demo.yaml` | writes nothing; the page keeps the visitor's key |
| Rebuild | on (loopback, or `--allow-settings`) | refused |
| Answer ratings | on (loopback, or `--allow-settings`) | off |
| Retrieval inspector | on (loopback, or `--allow-settings`) | on, read only, at the hosted rate |
| `--record` | supported | refused before the server starts |
| the sample databases | read-only | read-only |
| limits | none | a rate limit per visitor and a cap per session |

```bash
nl2sql demo --hosted --host 0.0.0.0 --port 7860
```

`NL2SQL_DEMO_HOSTED=1` does the same thing without a command line, which is how
a container turns it on.

## What the visitor's key does, and does not do

The visitor pastes a key on the **Settings** page. From there:

1. **The browser keeps it**, in that tab's `sessionStorage`. Closing the tab
   clears it. Nothing else in the page reads it.
2. **It travels as a request header**, `X-NL2SQL-Api-Key`, on `POST /api/ask`
   and nowhere else. A header rather than the body, because a body is what
   request logs and validation errors quote back. The playground is served over
   plain HTTP locally and over the host's TLS when it is published, so deploy it
   behind HTTPS.
3. **The server uses it for that one request.** It is bound to the request with
   [`nl2sql.llm.request_key`][key-module] and the LLM registry builds a client
   from it while the pipeline graph is constructed. The client is cached
   nowhere, so no later request and no other visitor can be handed it.
4. **Then it is gone.** It is never written to a file, never put in an
   environment variable, never held in a module-level cache, and never included
   in a response.

The key's shape picks the provider, exactly as `--api-key` does: `sk-ant-` is
Anthropic, `sk-or-` is OpenRouter, anything else is OpenAI. A key from a
different provider than the server's configured default also moves the model to
that provider's default, since a `gpt-` model means nothing to Anthropic.

What it does **not** do:

- It is not stored, so it cannot be reused for anyone else's question, and there
  is nothing to leak if the container is compromised later.
- It does not reach a trace. Run traces are written as usual (that is what the
  playground's **Debug** drill-down reads), and the trace redactor is told about
  the request's key along with every other credential, so it is masked if it ever
  reaches a prompt or an error.
- It does not reach the log. Nothing logs the header, and errors about a key
  report its type, never its value.
- It does not reach your data. The hosted demo answers only from the three
  sample databases shipped with the engine.

A question with no key answers `401` with a sentence telling the visitor to add
one under Settings. A malformed key answers `400` without quoting what was sent.

## Limits

Both live in this one process's memory. There are no accounts, no analytics and
no storage.

| Limit | Default | Environment variable | Keyed by |
| --- | --- | --- | --- |
| Questions per minute | 6 | `NL2SQL_DEMO_QUESTIONS_PER_MINUTE` | client address (a token bucket) |
| Questions per session | 30 | `NL2SQL_DEMO_QUESTIONS_PER_SESSION` | a random session cookie |
| Query timeout | 60s | `GLOBAL_TIMEOUT_SEC` | the run |
| Rows returned | 1000 | the datasource's `row_limit` option | the query |

Hitting one is a `429` with a sentence, never a stack trace: the rate limit says
to wait a moment, the session cap says to run the demo locally for no limit at
all. The retrieval inspector spends from the same rate bucket (it costs no
tokens, but it does embed text) and does not count against the session's
questions.

The session cookie is a random token this process made up. It names no visitor,
carries no key, and exists only so the session cap has something to count. A
visitor who clears their cookies starts a new session; the rate limit, which is
keyed by address, is what holds the pace down in the meantime.

## What is off, and what is on

**Off:** Settings persistence, Rebuild, answer ratings, and `--record` (refused
before the server starts). `--api-key` is refused too: a server-side key is the
one thing hosted mode is built to avoid, and any provider key exported into the
process is cleared at start-up so nothing can fall back to it.

**On:** asking the twenty guided questions or any other question, the schema
view, the per-node **Debug** drill-down with its traces, and the retrieval
inspector read-only.

## Read-only sample data

The demo's three SQLite databases are configured with `read_only: true` under
their connection options, which opens them through SQLite's own URI form with
`mode=ro`. The driver refuses a write before any SQL is parsed, under the RBAC
policy and the validator rather than instead of them. This is not specific to
hosted mode: nothing in the engine writes to a demo database, so they are opened
read-only everywhere.

## The container

`deploy/huggingface/Dockerfile` builds the playground as a hosted demo. It is a
Space root rather than a repository build: it needs no build context, so the
whole deploy is one `git subtree push` of that folder.

It installs `nl2sql-engine[demo]` (from the repository's `main` branch by
default; set `--build-arg NL2SQL_SPEC="nl2sql-engine[demo]==X.Y.Z"` to pin a
release), then runs `nl2sql setup --demo` **at build time**. That bakes three
things into the image so the container reaches nothing but the model at run
time and the first question is answered at once:

- the three sample databases and their configs,
- their vector index, built with the local embedder,
- the local embedding model itself, roughly 79 MB of ONNX `all-MiniLM-L6-v2`
  that chromadb would otherwise download on the first question. The 83 MB
  archive it was extracted from is deleted; chroma reaches for it only when the
  extracted files are missing.

The container runs as uid 1000 (not root), listens on `$PORT` (7860 by
default, which is what a Space routes to), and carries a healthcheck that polls
`/api/meta`. `NL2SQL_DEMO_HOSTED=1` is set in the image, so hosted mode holds
even if the command is overridden.

```bash
cd deploy/huggingface
docker build -t nl2sql-demo .
docker run --rm -p 7860:7860 nl2sql-demo
```

`docker compose up --build` in the same folder does it through
`docker-compose.yml`, which builds the same image with the same settings.

Measured on a build of this Dockerfile: **1175 MB** on the container's own
filesystem (`docker image ls` reports 1.65 GB, which includes the build
attestations). Most of it is chromadb's transitive dependencies (polars,
pyarrow, kubernetes, onnxruntime), which the vector store needs. Boot is
**about 5 seconds** from `docker run` to the first answered request, and a
guided question through a stand-in provider answered in **1.2 seconds**.

## Deploying it as a Hugging Face Space

The Space is `nadeem4nk/nl2sql-demo`: public, Docker SDK, CPU basic. The full
steps, and what each Space setting does, are in
[`deploy/huggingface/README.md`][space-readme] -- that file is also the Space's
own front page, since a Docker Space reads its configuration from the
front-matter of the `README.md` at its root.

In short, and performed by the owner:

1. Create the Space at <https://huggingface.co/new-space> under `nadeem4nk`,
   named `nl2sql-demo`, SDK **Docker** (blank), CPU basic, public.
2. Add it as a git remote:
   `git remote add space https://huggingface.co/spaces/nadeem4nk/nl2sql-demo`
   (pushing needs a write token from
   <https://huggingface.co/settings/tokens>).
3. Push the folder as the Space root:
   `git subtree push --prefix deploy/huggingface space main`.

The Space builds on push and serves at
<https://nadeem4nk-nl2sql-demo.hf.space>.

**Never give the Space an API key**, as a secret or otherwise. Hosted mode
clears any provider key it finds in its environment at start-up rather than use
it, because a key there would be spent by every visitor.

## Running it locally instead

Hosted mode exists to show the engine on our sample data. To ask questions of
your own data, with no limits and a key that never leaves your machine:

```bash
pip install "nl2sql-engine[demo]"
nl2sql demo
```

See [Demo](../getting_started/demo.md).

[key-module]: https://github.com/nadeem4/nl2sql/blob/main/packages/nl2sql/src/nl2sql/llm/request_key.py
[space-readme]: https://github.com/nadeem4/nl2sql/blob/main/deploy/huggingface/README.md
