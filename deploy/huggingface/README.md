---
title: nl2sql Playground
emoji: 🗄️
colorFrom: indigo
colorTo: gray
sdk: docker
app_port: 7860
pinned: false
license: mit
short_description: Ask a sample database in English, with your own API key
---

# nl2sql playground

Ask three small sample databases in plain English and watch the whole run: the
plan the model wrote, the checks it had to pass, the SQL, the rows, and what it
cost per step.

**Bring your own key.** This Space holds no API key. Open **Settings**, paste
your own OpenAI, Anthropic or OpenRouter key, and it stays in that browser tab:
it is sent with each question, used to answer that question, and stored nowhere
on the server. Closing the tab clears it.

**Your data never comes here.** The demo answers only from the three sample
databases baked into the image (a music store, its help desk and its website
analytics). They are opened read-only. To ask questions of your own data, run
it on your own machine:

```bash
pip install "nl2sql-engine[demo]"
nl2sql demo
```

Questions are limited to 6 a minute and 30 a browser session. Settings are not
saved, the index cannot be rebuilt, and answers cannot be rated: everything
that would write to this server's disk is off.

Source and documentation: <https://github.com/nadeem4/nl2sql>.

---

## Deploying this Space (for the repository owner)

Everything the Space needs is in this one folder: the `Dockerfile` builds the
image with no build context from the repository, and the front-matter above is
the Space configuration. Nothing here holds a secret, and no agent performs the
deploy.

**1. Create the Space.** At <https://huggingface.co/new-space>, under the
`nadeem4nk` account:

| Field | Value |
| --- | --- |
| Owner | `nadeem4nk` |
| Space name | `nl2sql-demo` |
| License | MIT |
| SDK | **Docker** → *Blank* |
| Hardware | CPU basic (free) |
| Visibility | **Public** |

That gives <https://huggingface.co/spaces/nadeem4nk/nl2sql-demo>, empty.

**2. Add it as a remote**, from a clone of this repository:

```bash
git remote add space https://huggingface.co/spaces/nadeem4nk/nl2sql-demo
```

Pushing needs a Hugging Face access token with **write** permission
(<https://huggingface.co/settings/tokens>). Git asks for it as the password,
with `nadeem4nk` as the username; `huggingface-cli login` stores it for you.

**3. Push this folder as the Space root.** The Space expects its `Dockerfile`
and `README.md` at the top level, which is what this subtree push gives it:

```bash
git subtree push --prefix deploy/huggingface space main
```

The Space builds on push; watch the log on its **Logs** tab. A first build takes
a few minutes (installing the engine, generating the sample databases, indexing
them and baking in the embedding model). The playground is live at
<https://nadeem4nk-nl2sql-demo.hf.space> once the build is green.

To deploy again after a change here, run the same `git subtree push`. If it is
refused because the Space has commits of its own,
`git push space $(git subtree split --prefix deploy/huggingface main):main --force`
replaces its history with this folder's.

**4. Check it.** Open the Space, go to **Settings**, paste a key, and ask a
guided question. Without a key the page answers `401` with a sentence pointing
at Settings; that is the error visitors see before they add one.

### Which engine version it builds

The `Dockerfile` installs the engine from this repository's `main` branch by
default, so the Space tracks what is merged. To pin a release instead, set the
build argument in the `Dockerfile`:

```dockerfile
ARG NL2SQL_SPEC="nl2sql-engine[demo]==0.2.0"
```

### Settings on the Space

None are required. The image sets them all, and the Space's **Settings →
Variables** can override any of them:

| Variable | Default | What it does |
| --- | --- | --- |
| `NL2SQL_DEMO_HOSTED` | `1` | hosted mode; never turn this off on a public Space |
| `PORT` | `7860` | the port the Space routes to (`app_port` above must match) |
| `NL2SQL_DEMO_QUESTIONS_PER_MINUTE` | `6` | rate limit per visitor |
| `NL2SQL_DEMO_QUESTIONS_PER_SESSION` | `30` | cap per browser session |
| `GLOBAL_TIMEOUT_SEC` | `60` | how long one question may run |
| `TRACE_MODE` | `always` (from `.env.demo`) | what the playground's **Debug** drill-down reads. Every question writes a trace file to the container's own disk, which is wiped whenever the Space restarts; set it to `on_failure` to keep only the runs that went wrong |

**Never add an API key as a Space secret.** The whole point of hosted mode is
that the server has none; a key here would be spent by every visitor, and
`nl2sql demo --hosted` clears any provider key in its environment at start-up
rather than use it.

## Testing the same image locally

```bash
cd deploy/huggingface
docker compose up --build
```

Then open <http://localhost:7860>. `docker compose` uses this same
`Dockerfile`, so what you see locally is what the Space runs.
