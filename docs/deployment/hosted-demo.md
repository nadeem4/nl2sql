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
| the API keys | saved to `.env.demo`, used by the process | one per provider, held by the browser tab, sent per request, used in memory, dropped |
| a model per step | written to `configs/llm.demo.yaml` | chosen in the browser tab, sent per request, saved nowhere |
| Settings | writes `.env.demo` and `configs/llm.demo.yaml` | writes nothing; the page keeps the keys and the choices |
| Rebuild | on (loopback, or `--allow-settings`) | refused |
| Answer ratings | on (loopback, or `--allow-settings`) | off |
| Retrieval inspector | on (loopback, or `--allow-settings`) | on, read only, at the hosted rate |
| Pipeline page | on | on, read only: it names the steps and their models, never a key |
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
with no reload. That state is the only place the point is made: until a key is
saved the top bar reads just **Hosted demo.**, and it says whose key answers
and what the limits are once there is one. None of this appears in local mode.

The visitor pastes a key on the **Settings** page, one per provider. From there:

1. **The browser keeps it**, in that tab's `sessionStorage`, under that
   provider's own name. Closing the tab clears it. Nothing else in the page
   reads it.
2. **It travels as a request header of its own**, `X-NL2SQL-Api-Key-<provider>`
   (`X-NL2SQL-Api-Key-anthropic`), on `POST /api/ask` and nowhere else. A
   header rather than the body, because a body is what request logs and
   validation errors quote back; one header per provider, so a key never shares
   a header with anything else and nothing that parses or quotes a header can
   expose two at once. With exactly one key the older `X-NL2SQL-Api-Key` is
   sent as well, and on its own it still means what it always did. The
   playground is served over plain HTTP locally and over the host's TLS when it
   is published, so deploy it behind HTTPS.
3. **The server uses it for that one request.** It is bound to the request with
   [`nl2sql.llm.request_key`][key-module] and the LLM registry builds each
   step's client from it while the pipeline graph is constructed. The client is
   cached nowhere, so no later request and no other visitor can be handed it.
4. **Then it is gone.** It is never written to a file, never put in an
   environment variable, never held in a module-level cache, and never included
   in a response.

A key sent without naming a provider has its provider read from its shape,
exactly as `--api-key` does: `sk-ant-` is Anthropic, `sk-or-` is OpenRouter,
anything else is OpenAI. Such a key also moves the model to that provider's
default when it differs from the server's configured one, since a `gpt-` model
means nothing to Anthropic.

## A model for each step

Choosing a model asks the server to remember nothing, so hosted mode keeps it.
Under **Models for each step** on the Settings page, collapsed by default, the
visitor can put each of the five model-using steps on a provider and model of
their own. The choice lives in the same tab's `sessionStorage` and travels with
each question in one more header:

```
X-NL2SQL-Models: {"astplanner":"anthropic:claude-opus-5"}
```

A compact JSON object, agent name to `provider:model`, and nothing else; it
carries no secret, which is why it is the one header the server parses and the
one thing a refusal repeats. A step with no entry runs on the configured
default, so the simple path stays one key, the defaults, and a question.

The server checks every name against what the engine knows -- the pipeline's
own steps, and the verified model lists in `nl2sql/llm/providers.py` -- before
anything reaches a client. Each step is then built from the key for the
provider it names: the planner can be on Claude while the answer writer stays
on OpenAI, each calling its own endpoint with its own key.

**A step whose chosen provider has no key is refused before the question
runs**, with `400` and a sentence naming both, for example *"The Query planner
step is set to run on Anthropic, but no Anthropic key was supplied. Add one
under Settings, or put that step back on a provider you have a key for."*
Nothing is spent on the keys that were supplied. A step nobody chose a provider
for is never refused this way: it takes the key for the provider it is
configured on, or, failing that, the first key the request brought, exactly as
a single key has always worked.

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

A question with no key at all answers `401` with a sentence telling the visitor
to add one under Settings; the page holds the controls closed before it comes
to that, so the `401` is the guard rather than the first thing a visitor meets.
A malformed key answers `400` without quoting what was sent, and no refusal
ever repeats something shaped like a key, not even when one was pasted into the
model header by hand.

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

**Off:** Settings *persistence* (choosing a model is on; it is only the saving
that is off), Rebuild, answer ratings, and `--record` (refused
before the server starts). `--api-key` is refused too: a server-side key is the
one thing hosted mode is built to avoid, and any provider key exported into the
process is cleared at start-up so nothing can fall back to it.

Rebuild being off is said on the page rather than left as a gap: the index
panel reports that the index was built before the demo started and which
databases it covers, and where the button would be it prints the server's own
reason -- rebuilding writes to disk and the sample data never changes.

**On:** asking the twenty guided questions or any other question, a key per
provider and a model per step (both kept by the browser), the schema view of
any of the three databases (the rail's **Showing** switcher), which database
answered a run, the per-node **Debug** drill-down with its traces, and the
retrieval inspector read-only.

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

It installs `nl2sql-engine[demo]` from **one commit of this repository**, named
by the `NL2SQL_REF` build argument -- `main` in the checked-in `Dockerfile`, and
the deploying commit's sha in the copy the workflow pushes to the Space. Set
`--build-arg NL2SQL_SPEC="nl2sql-engine[demo]==X.Y.Z"` to install a release
instead. Then it runs `nl2sql setup --demo` **at build time**. That bakes three
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
stamps it with the commit being deployed, and waits for the rebuild.

**What a deploy puts at the Space root.** The four files of
`deploy/huggingface/` -- `Dockerfile`, `README.md` (the Space's configuration
and front page), `docker-compose.yml`, and `SOURCE_SHA` -- with two of them
rewritten to name the commit:

| At the Space root | What the deploy writes |
| --- | --- |
| `SOURCE_SHA` | the full sha of the repository commit this deploy came from |
| `Dockerfile` | its `ARG NL2SQL_REF=` line, rewritten from `main` to that same sha |

Both matter, and for different reasons.

`SOURCE_SHA` is what makes an **engine-only or playground-only change reach the
Space at all**. The Space repo holds only that one folder, so a merge that
changed `packages/nl2sql/` left the mirrored files byte-identical: there was
nothing to commit, the Hub saw no new commit, and it never rebuilt. The Space
stayed frozen on whatever `main` was the last time the folder itself happened to
change. Stamping the sha means every deploy is a real commit, and a real commit
is what the Hub rebuilds on.

The `Dockerfile` rewrite is what makes that rebuild **build the right thing**.
The image installs the engine from a GitHub archive of this repository, and
building `main` meant the Space got whatever `main` was at build time rather
than what the deploy was for. The ref is now the deploying sha, so the Space is
pinned to its own source commit -- and because the text of the `ARG` line
changes, the layer cache for the install below it is busted and the engine is
genuinely reinstalled. The workflow rewrites the default rather than passing
`--build-arg`, because a Space build takes no build arguments from us; a `grep`
right after the rewrite fails the deploy if that line is ever renamed, instead
of quietly shipping a Space that still builds `main`.

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
engine the image installs) or `web/playground/**` (the page the engine serves)
-- and on demand. A docs-only merge changes none of those and does not
redeploy. One deploy runs at a time; a run overtaken by a newer push is
cancelled.

**What it reports.** The job never calls a build that did not happen a success.
If the push produced a commit, the workflow checks the Space's head is that
commit, then waits for a build to actually start and finish; a Space that never
starts one within five minutes fails the job rather than reporting the previous
build's `RUNNING`. If there was nothing to push -- the same commit deployed
twice -- the job says so plainly and the summary reads **"No rebuild"**, with
the Space left on its previous image.

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
  again, stamps the Space with **that** sha, and the image is rebuilt from it --
  so this rolls the engine and the playground back, not just the Space's
  configuration. (Before the sha was stamped in, the image installed the tip of
  `main` and a re-run restored only the configuration; that is fixed.)
- **Push an earlier subtree yourself**, with the manual steps below:
  `git push space $(git subtree split --prefix deploy/huggingface <old-sha>):main`.
  Doing it this way ships the checked-in `Dockerfile`, whose ref is `main`, so
  the Space rebuilds from the tip of `main` rather than from `<old-sha>`. Edit
  `SOURCE_SHA` and the `ARG NL2SQL_REF=` line yourself if you want the engine
  pinned too -- or just use the re-run above, which does it for you.

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
