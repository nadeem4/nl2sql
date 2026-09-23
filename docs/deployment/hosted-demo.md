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

A visitor who has not pasted one yet is told so before they ask. The **Ask**
page opens on a short state saying the demo runs on their own key, that it
stays in this browser tab and is never stored on the server, with a link to
**Settings**; the question box and the guided questions are disabled until
there is a key, so a click cannot fail with a `401` the visitor had no way to
see coming. The moment a key is saved the state clears and everything enables,
with no reload. None of this appears in local mode.

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
  reaches a prompt or an error. The demo project ships `TRACE_MODE=always`, so
  every question leaves a file on the container's own disk; `TRACE_MODE=on_failure`
  keeps only the runs that went wrong.
- It does not reach the log. Nothing logs the header, and errors about a key
  report its type, never its value.
- It does not reach your data. The hosted demo answers only from the three
  sample databases shipped with the engine.

A question with no key answers `401` with a sentence telling the visitor to add
one under Settings; the page holds the controls closed before it comes to that,
so the `401` is the guard rather than the first thing a visitor meets. A
malformed key answers `400` without quoting what was sent.

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

Rebuild being off is said on the page rather than left as a gap: the index
panel reports that the index was built before the demo started and which
databases it covers, and where the button would be it prints the server's own
reason -- rebuilding writes to disk and the sample data never changes.

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
whole deploy is that one folder, copied to the Space's root.

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

The Space builds on push and serves at
<https://nadeem4nk-nl2sql-demo.hf.space>.

**Never give the Space an API key**, as a secret or otherwise. Hosted mode
clears any provider key it finds in its environment at start-up rather than use
it, because a key there would be spent by every visitor.

### Automatically, from `main`

[`.github/workflows/publish_space.yml`][workflow] is the normal path: it creates
the Space if it is missing, mirrors `deploy/huggingface/` onto the Space's root,
and waits for the build.

**The one secret.** A Hugging Face access token with **write** permission
(<https://huggingface.co/settings/tokens>), added as the repository secret
**`HF_TOKEN`** at
<https://github.com/nadeem4/nl2sql/settings/secrets/actions>. That is the only
credential the workflow uses, and nothing else needs configuring. Without it --
on a fork, or before it is added -- the job logs a line saying so and finishes
green; it never fails for a missing secret.

The token is read into the job's environment and used in exactly two places: by
`huggingface_hub`, which picks `HF_TOKEN` up from the environment on its own,
and as the password in the `git push` URL. It is never echoed, never traced (no
`set -x`), and never written to a file: the Space remote is passed to each git
command rather than saved into `.git/config`.

**When it runs.** On every push to `main` that touches something the Space is
built from -- `deploy/huggingface/**` (its root), `packages/nl2sql/**` (the
engine the image installs from `main`) or `web/playground/**` (the page the
engine serves) -- and on demand. A docs-only merge changes none of those and
does not redeploy. One deploy runs at a time; a run overtaken by a newer push is
cancelled.

**By hand.** Actions → **Publish Space** → *Run workflow*. Two optional inputs:
`space_id`, which defaults to `nadeem4nk/nl2sql-demo`, and `token_secret`, the
name of the secret holding the token, which defaults to `HF_TOKEN`. Pointing
`space_id` at a scratch Space of your own is how to rehearse a change without
touching the public demo.

**What the first run does.** The Space does not have to exist. The workflow
calls `create_repo(repo_type="space", space_sdk="docker", private=False,
exist_ok=True)`, which makes a public Docker Space on CPU basic, then pushes the
folder into it. On every later run `exist_ok` makes that call a no-op: the Hub
ignores visibility, SDK and hardware for a Space that already exists, so a
hardware upgrade or a visibility change made in the Space's own settings
survives a deploy.

**Rolling back.** Two ways, both without touching the Hub by hand:

- **Re-run an older commit's workflow.** Actions → **Publish Space** → the run
  for the commit you want back → *Re-run all jobs*. It checks that commit out
  again and pushes its `deploy/huggingface/` to the Space. Note that the image
  installs the engine from the **tip of `main`** unless the `Dockerfile` pins a
  release, so this rolls back the Space's configuration, not necessarily the
  engine inside it; pin `NL2SQL_SPEC` to a released version if you need the
  whole thing to go back.
- **Push an earlier subtree yourself**, with the manual steps below:
  `git push space $(git subtree split --prefix deploy/huggingface <old-sha>):main`.

The workflow adds a commit on top of whatever the Space's `main` already is, so
the Space's history is never rewritten and never lost -- including commits made
in the Hub's own web editor. If a concurrent push lands between the workflow's
fetch and its push, the push is refused and the job fails; re-running it picks
up the new head and reapplies the folder. Nothing in the workflow force-pushes.

### By hand, as a fallback

If the workflow is unavailable, the owner can do the same thing from a clone:

1. Create the Space at <https://huggingface.co/new-space> under `nadeem4nk`,
   named `nl2sql-demo`, SDK **Docker** (blank), CPU basic, public.
2. Add it as a git remote:
   `git remote add space https://huggingface.co/spaces/nadeem4nk/nl2sql-demo`
   (pushing needs a write token from
   <https://huggingface.co/settings/tokens>).
3. Push the folder as the Space root:
   `git subtree push --prefix deploy/huggingface space main`.

If step 3 is refused because the Space has commits of its own,
`git push space $(git subtree split --prefix deploy/huggingface main):main --force`
replaces the Space's history with this folder's. The workflow above never needs
that, which is why it exists.

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
[workflow]: https://github.com/nadeem4/nl2sql/blob/main/.github/workflows/publish_space.yml
