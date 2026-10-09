# nl2sql playground UI

The React source for the page `nl2sql demo` serves.

## Pages

Five pages, named across the top of each of them by one header nav
(`.nav`, one `#nav-home`, `#nav-ask`, `#nav-pipeline`, `#nav-settings`,
`#nav-retrieval` link each). Every page but Home states its title and one line
saying what it does, then gives its content the full width; Home is its own
hero.

| Route | Page | What it holds |
| --- | --- | --- |
| `#/` | Home | `#home`: the front page (see [Home](#home) below) |
| `#/ask` | Ask | the composer, the rail (Database, with a switcher for which database, then the Search index as one line) and the run |
| `#/pipeline` | What runs a question | `#pipeline-panel`: every step, and which five call a model |
| `#/settings` | Settings | `#settings-panel`: keys and a model per step |
| `#/retrieval` | Retrieval inspector | `#retrieval-panel`: the inspector |

`src/router.js` is the whole of it: `pageFromHash` reads the hash, `navItems`
builds the nav, and `createRouter` follows `hashchange`. A route nobody serves
reads as `#/`, Home, so Back and Forward always land somewhere. A bare fragment
from an older link (`#run`, `#question`, a station's id) named a place on Ask
when Ask was `#/`, so it still opens Ask. The nav links are
plain `<a href="#/...">`, so the keyboard reaches them and the browser keeps
the history; the current one carries `aria-current="page"` and the accent under
the top bar's rule. Nothing in the page links to a bare fragment -- the skip
control and the index warning's **Rebuild it** move focus instead -- because
the hash belongs to the router.

**The run is not lost.** The question, the answer, the Debug choice and every
`/api` reply are held in `App`, above the page switch, so Settings and back
leaves the run exactly as it was. Nothing extra is written to storage for it:
the address bar already restores the page on a reload.

**Where a page is off** (Settings, Retrieval and Rebuild share one gate: a demo
project, on a loopback host or with `--allow-settings`), its nav item stays and
is marked `off`; opening it is how you read the reason
(`#settings-unavailable`, `#retrieval-unavailable`).

**Hosted mode** (`nl2sql demo --hosted`, `meta.hosted`) is a third state, not a
page that is off. Settings keeps its nav item unmarked and shows two blocks
that write only to this tab's `sessionStorage`, never to the server:

- `#hosted-key-heading`, one key per provider (`src/hostedKey.js`), in a
  raised card: a provider choice (OpenAI, Anthropic, OpenRouter, each saying
  "key set" or "none"), a 44px mono `#hosted-key` and **Use this key**. A pasted
  key is kept under the provider its own prefix names, even when another card
  was chosen, and the page says so (`keyMismatch`). Each held key shows as
  "Key ending …0f3a is active in this tab" (`#hosted-key-current`, the last four
  characters only, `keyTail`) with its own **Clear** (`#hosted-clear-<provider>`).
  Three facts -- Stored, Sent, Never -- stand for the privacy text, which is in
  full behind **How your key is handled** (`#hosted-key-help`), with the limits
  and how to run locally. `askHeaders` sends each as its own
  `X-NL2SQL-Api-Key-<provider>` header on `/api/ask`. With exactly one key the
  bare `X-NL2SQL-Api-Key` goes too, so the simple path is byte for byte what it
  was.
- `#hosted-models`, **Model for each step** (`src/hostedModels.js`), a
  `<details>` card, open from the start at 1060px and wider (where it sits
  beside the key card) and shut on a narrower window; its summary says what the
  steps will use. Each
  of the five model steps has a selector over the same catalogue the local page
  offers, which hosted mode's `GET /api/settings` now carries (`nodes` and
  `providers`, and still not a key of any kind). The choices travel as one
  `X-NL2SQL-Models` header, `{"astplanner":"anthropic:claude-opus-5"}`, which
  holds no secret. A step on a provider this tab has no key for says so
  (`#hosted-model-<agent>-nokey`), and the server refuses the question by name.

Nothing about a key or a choice is posted to the server, so there is no save to
succeed or fail; the copy on the page says exactly that, plus the limits from
`meta.limits` and how to run the demo locally instead. Rebuild and answer
ratings are off, and the retrieval inspector stays on. See
[Hosted demo](../../docs/deployment/hosted-demo.md).

**Hosted, before a key** (`needsKey` in `src/firstRun.js`: `meta.hosted` and
nothing in this tab's `sessionStorage`), the Ask page opens with `#first-run`
above the question box: the pitch and `#first-run-why` (`firstRunCopy`), then
the guided questions as chips headed **Try a recorded run**, moved up from
under the question box so they lead. The ones the server has recordings for
(`meta.recorded`, `isRecorded`) carry a dot and `data-recorded`. The key form
sits in `#first-run-keyform`, a `<details>` folded under **Use your own key**
when there are recordings and open when there are none: a provider choice,
`#first-run-provider-<provider>`, the key, `#first-run-key`, **Use this key**,
`#first-run-save`, and three facts (`#first-run-facts`: **Stored** in this tab
only, **Sent** in a header with each question, **Never** written to disk, logs
or traces). The provider follows the key's own prefix once one is typed, as the
server reads it, and the key is kept exactly as the Settings form keeps it
(`writeKeyFor` in `src/hostedKey.js`); the keyboard then moves to `#question`.
**Nothing is disabled** (`lockControls`): a recorded guided question answers
from its recording, and the answer header carries a **Recorded run** badge
(`#recorded-badge`, `recordedBadge`) whose title says it was not run just now;
any other question comes back as a replay miss, shown as `#replay-miss` with
**Add a key in Settings** (`#replay-miss-key`, `missPrompt`). The Cost & time
station's footnote says a recorded run's token counts are placeholders.
Saving a key here or in Settings clears all of it on the next render, with no reload:
`App` holds the key in state, so nothing has to be reloaded to see it. In local
mode none of this appears -- there a key is already configured, or replay
answers from recordings, and a wall would be in the way of someone who has
nothing to do.

`src/router.test.js` covers the default route, deep links, an unknown route,
Back and Forward, and that routing touches nothing but the hash.

## Home

`#/` (`src/Home.jsx`, its words in `src/home.js` so `npm test` can hold each
claim against what the playground does). Top to bottom:

- **The hero**: the one eyebrow on the page (**Questions in, rows out**), the
  promise as the h1, one sentence a line on a wide window, a lede, then three
  actions. **Try a sample question** (`#home-try`) opens Ask and asks the first
  guided question the server has a recorded answer for (`sampleQuestion`), so
  on the hosted demo it answers with no key; with nothing recorded it asks the
  first guided question, which answers with the key prompt. **Use your own
  key** (`#home-key`) is `#/settings`. **See how it works** (`#home-how`) moves
  focus to the path below rather than linking to a fragment. Under them three
  facts (`#home-facts`, `facts`); the third depends on the server, and only
  says a key is optional where something answers without one.
- **The path** (`#how`): Question, Plan, Check, SQL, Rows, drawn as the run
  spine laid on its side and with its marks: round for a step a model decides,
  square for code, and the checks as the gate bar. Its line draws and the marks
  fill in order on load, the page's one moment of motion (held still under
  reduced motion). On a phone it stands up, like the spine on Ask.
- **Three features**, alternating sides: **Ask, and get rows back**, **Watch
  the plan become SQL** and **See what it looked up**, each with two points,
  a link into its page and a clip.
- **More**: run it locally (`pip install "nl2sql-engine[demo]"`, `nl2sql demo`),
  the docs, the source; then a footer.

**Clips** (`src/Clip.jsx`): a muted, looping, inline webm over its poster in
the one raised card, with a 44px Pause / Play button. It follows
`prefers-color-scheme`, swapping to the `-dark` recording and poster; under
reduced motion it holds the poster until Play. A clip with no video yet (the
request fails) shows its poster alone, with no button. The files are served by
the app at `/clips/<name>` from
`packages/nl2sql/src/nl2sql/cli/demo/playground/assets/clips/`, outside the
bundle (which `npm run build` rewrites and which should not carry video) and
named `<feature>[-dark].webm|jpg` for `ask`, `pipeline` and `retrieval`.

They are recorded, not drawn:

```bash
pip install playwright && python -m playwright install chromium
python scripts/record_home_clips.py
```

The script boots `nl2sql demo --hosted` with no key anywhere, drives each page
in headless Chromium at 1280x800 and writes a webm and a poster per feature
and theme (each well under 2 MB; a test holds that). Pipeline and Retrieval
call no model, so they are always real. Ask shows a model's answer, so it is
recorded only from a guided question with a shipped recording
(`scripts/record_demo_answers.py`): the shipped clip replays the first guided
question with one, under its "Recorded run" badge. Without a recording it falls
back to a "Clip coming" poster in both themes. Actions → **Record demo answers** → *Run workflow*
with `record: clips` runs the same script in CI and opens a pull request.

## What the page shows

- **Database** (left rail, or below the run on a narrow window): the indexed
  schema from `/api/schema`, visible before any question. It shows one database
  at a time, and with more than one registered the heading carries a switcher
  (`#schema-datasource`, the names from `datasourceNames` over `meta.datasources`:
  a segmented control of radio buttons for four or fewer, a select beyond that,
  `switcherKind` in `datasources.js`) that re-reads `/api/schema?datasource=`; under it one line says plainly that
  each question is answered from one database and that joining across them is
  planned (`#schema-cross`). Clicking a guided question from another pile moves
  the switcher to that pile's database, so the rail shows the schema the
  question is about. With a single database none of that is printed: the
  heading names it and the panel reads exactly as it always has.
  Each table is a 40px row with its row count; open one for the tables it
  refers to, columns, types, keys and foreign keys. Tables the current plan
  reads are marked `in plan` with an accent bar; tables the role was refused
  are marked `refused for <role>`. On a wide window the rail scrolls on its own:
  its scroll is contained, its scrollbar is thin, and a fade shows only on an
  edge that has more past it (`src/railFade.js`). It reserves no scrollbar
  gutter: `scrollbar-gutter: stable` beside the thin scrollbar left Chrome
  painting over the first number on a line (the table count, the first index
  stat). The rail's headings (Database, Search index) are small uppercase
  labels like the composer's YOUR QUESTION.
- **Search index** (`#index-panel`, foot of the rail): from `GET /api/index`,
  the vector index the resolver searches, which the Database above does not
  show. Folded to one line ("Search index fresh. 162 entries, built 3 minutes
  ago.", `indexSummary` in `indexHealth.js`) that opens itself when the index
  is empty, missing or stale or a rebuild is running or failed
  (`indexExpanded`); `#index-heading` stays, visually hidden. Opened: a status line (`#index-status`), entries by type (`#index-counts`), the
  schema version (`#index-version`) and when it was built (`#index-built`). The
  index covers every database, so the heading names one only when there is one;
  with several, `#index-sources` lists them ("Covers chinook, support and
  webanalytics", from `coverageLine` in `indexHealth.js`) and
  Rebuild's help names the single database it rebuilds. Hosted, that line is
  always printed and says the index was built before anyone arrived ("Built
  before this demo started, covering chinook, support and webanalytics"), so
  the missing Rebuild reads as a decision rather than as something broken.
  When the index is empty, missing or out of date, the panel turns to the fault
  colour, a stale index lists why, and a warning under the top bar
  (`#index-warning`) opens the panel and puts the keyboard on the button, or, from another page,
  leads back to Ask. **Rebuild** (`#index-rebuild`) is
  always offered; it posts to `POST /api/index/rebuild` and the page polls
  `GET /api/index` for its steps (`#index-progress`) until it ends, then
  re-reads the schema. **Write descriptions with the LLM** (`#index-enrich`) is
  off by default and disabled without a key. A failure shows `#index-error`.
  Where Rebuild is off (a non-loopback `--host` without `--allow-settings`, or
  the hosted demo) the panel says why in the server's own words
  (`#index-unavailable`, `rebuild.reason` from `/api/index`); hosted, that
  sentence already says the sample data never changes and what to run instead,
  so the terminal command is left off. A demo folder written by an older
  engine shows `#index-folder-warning`.
- **Status pill** (`.status`, top bar, on the same row as the wordmark and the
  nav at 1061px and wider): the mode in a few words, from `modeStatus` in
  `src/status.js`, with a green dot when a question can be answered live and an
  amber one when it cannot. **Live · configured model**; **Replay mode · 12 of
  20 recorded** (`recorded_questions` from `/api/meta`) or **Replay mode · No
  recordings**; hosted, **Hosted demo · Recorded runs** (or **Hosted demo · No
  key yet** when nothing is recorded) until this tab has a key,
  then **Hosted demo · OpenAI key in this tab** (or **2 keys**). The sentence
  the old mode line carried -- how many questions replay answers and how to
  get a key, or whose key answers and the hosted limits (`hostedNote` in
  `src/firstRun.js`) -- is kept whole as the pill's `title` and as
  visually-hidden text a screen reader reads. A question replay has no answer
  for shows "No recorded answer for this question. Add an API key to ask it
  live." (`replay_miss` from `/api/ask`).
- **Composer** (Ask page): the question box, the role selector
  (`#role-select`), **Plan only** (`#plan-only`), **Debug** (`#debug-toggle`)
  and the guided questions from `/api/meta` as a chip row (`#guided-heading`):
  four from the database the rail is showing (three once a run is on the
  page) and a **N more** chip (`aria-controls="guided-all"`) that opens every
  pile, one `.guided-group` per datasource headed by its id (`#guided-<ds>`,
  always in the page, visually hidden with a single database). Under 640px the
  row is a horizontal scroll-snap strip of 44px chips. `chipRow` in
  `src/runState.js` picks the chips; `src/questions.js` does the grouping and
  falls back to the flat `questions` list when a server sends no
  `question_groups`. With a single database the question box names it; with
  several it does not, because the resolver picks. An empty box with a key
  leaves **Ask** looking ready; pressing it puts the keyboard in the box.
- **Busy and Stop**: while `/api/ask` is out, only the first unfinished
  station is live (the API reports no per-node progress, so that is Plan) and
  the rest are queued at 40% opacity, with skeleton bars in the live one, a 2px
  accent segment travelling down the spine (`.spine-run`, held still under reduced
  motion) and `#elapsed` beside the button: **Running · 3.4 s**, drawn on
  `requestAnimationFrame`, with a visually hidden status that changes once a
  second. **Ask** (`#ask`) keeps its fill; after 600ms (`STOP_AFTER_MS`) it
  becomes **Stop**, which aborts the fetch through an `AbortController`. The
  run then reads **Stopped** and every station is marked not reached. The
  server watches the connection and cancels the run when it closes
  (`cancel_when_disconnected` in `app.py`, an `nl2sql.CancellationToken`
  underneath): no further step or model call starts. A stopped question still
  counts toward the hosted limits, which charge it on arrival. The answer
  header says so (`stoppedNote` in `src/runState.js`).
- **The answer header** (`#pane-question`, the first block of the run): before
  a question, one line saying the answer will lead here. Once asked, the
  question with its role tag, then the answer sentence at 28px (the largest
  text in the run; the Rows station keeps only the table), then a mono strip
  such as `10 rows / 3 of 3 checks / 2 plans / 5.04 s / $0.0187`. Each strip
  item is a button that scrolls to its station and focuses it; none is a hash
  link, because the hash is the router's. A refusal gets the same header
  ("Refused at the checks. No SQL was written."). `answerHead` and
  `statusStrip` in `src/runState.js` decide the words. On the Ask page
  `#page-title` is visually hidden, kept for `aria-labelledby`.
- **Faults**: a failed run turns the header into the fault card, a 3px
  `--fault` rule on the raised card. `describeFault` in `src/faults.js` gives
  the headline (the only `role="alert"`), the body and an action, such as
  **Replace the key** to `#/settings` for `PROVIDER_AUTH_FAILED`; **Ask again**
  sits beside it, and the provider's own reply (`provider_response`) is folded
  under **Provider response** in 12px mono. `runFault` picks the entry: any
  `PROVIDER_*` error, or the first error when no sub-query came back.
- **The run**: one spine, read top to bottom: Plan (`#pane-plan`), Checks
  (`#pane-validation`), SQL (`#pane-sql`), Rows (`#pane-rows`), Cost & time
  (`#pane-usage`). Each station's head is a 12px uppercase label, a kind tag
  (**Model · gpt-5.4** from `usage.nodes`, or **Code**) and the step's time on
  the right (`stationHeads`). Marks are round for a model step and square for
  code; filled with a tick is done, an outline is pending, dashed was not
  reached. The checks are a gate band: a timeline of the plans the checks saw
  (**Plan 1 refused**, **Plan 2 passed**, `gateTimeline`), the reason in one
  sentence (`gateReason`: the refusal, the tables the role may not read, or
  what sent a plan back and the refiner's hint), and each check as a tile with a
  tick or a cross, passed in `--ok` and refused in `--fault`. The SQL and Rows
  stations then show that nothing was written or run. With more than one
  database registered, the answer header also says which one answered
  (`#answered-from`, from each sub-query's `datasource_id` in `/api/ask` via
  `answeredDatasources`); with one there is nothing for the resolver to have
  picked, so the line is not printed.
- **SQL and rows** are raised cards. The SQL card (`src/SqlCard.jsx`) has a
  header strip, **Generated SQL** and the plan it came from (`from plan 2` after
  a retry), a **Copy** button (the clipboard, or the text selected where the
  browser refuses; it reads "Copied" for about 1.4s), numbered lines and a
  keyword tint: keywords, function names and numbers take `--kw`, `--fn` and
  `--num`. The tint comes from `src/sqlHighlight.js`, a small tokenizer with no
  library, rendered as React text so the SQL is never parsed as markup; a long
  line scrolls inside the card. The rows card (`src/RowsTable.jsx`) fills the
  column with 38px rows (44px on a phone) and a header that sticks. The first
  numeric column carries an in-cell bar, a 6px track and fill sized to the
  column's largest magnitude (a negative is drawn by its size, in ink), beside
  the right-aligned number. NULL stays dimmed. The foot counts the rows and
  offers **Download CSV**, built in the page from the rows it already has.
- **Cost & time**: five figures (total time, waiting on the model, model calls,
  input tokens with the cached count, cost). With **Debug** on, a per-node
  ledger follows (`src/Ledger.jsx`): one row per node that ran, in execution
  order, code nodes included, with calls, input, cached and output tokens
  (reasoning too when any node spent some), LLM time and node wall-clock time.
  Beside each node time is a 72x6px bar sized to the slowest node: a model
  node's bar is in the accent, a code node's in dimmed ink. A node that needed
  more than one call is tagged **retried**. The SQL agent is a subgraph, so its
  nodes are nested under it and its time includes theirs. When a sub-query's plan came from the
  plan cache (`plan_source: "cache"` in `/api/ask`), Debug also shows
  `#plan-cache-hit`: no planner call was made, and the plan was validated again
  for the role. Debug is on by default; the choice is
  kept in `localStorage` (`nl2sql.playground.debug`) and the page works the same
  when storage is unavailable.
- **Node drill-down**: when the run wrote a trace (`trace_path` in the
  response; the demo writes one for every run), each node name in the Debug
  table is a button. It fetches the trace once from `GET /api/trace/{trace_id}`
  and shows that node's executions (attempts and sub-queries) with errors and
  warnings, the state it read, the update it returned and, for LLM nodes, the
  exact prompt, the raw response and the parsed result, each folded. A
  **Download trace** link sits above the table. Opening a node scrolls the
  inspector (`#node-inspector`) just into view and moves focus to its title
  (`#inspect-title`); **Close** returns focus to the node that opened it.
- **Ask another question** (`.ask-dock`, phones only): once a run is on the
  page, a bar fixed to the bottom of the screen scrolls back to `#question`
  and focuses it. It hides while `#question` is in view.
- **Was this answer right?** (`#feedback`, below Cost & time once a run is
  back): **👍 Right** (`#feedback-up`) and **👎 Wrong** (`#feedback-down`) save
  a rating at once through `POST /api/feedback`; a note is optional, from the
  quick choices or typed (`#feedback-note`, up to 280 characters), saved with
  **Save note** (`#feedback-save`). The page sends only the trace id, the
  rating and the note; the server stores the question, role, SQL and models
  from its own copy of the run. Whether it is on comes from `GET /api/feedback`;
  where it is off (the same rule as Settings, or `FEEDBACK_ENABLED=false`) a
  line says why. See
  [Feedback and Signals](../../docs/observability/feedback.md).

- **Pipeline** (`#nav-pipeline` in the header nav, route `#/pipeline`; the
  page's content is `#pipeline-panel`): every step of a run in order, in three
  phases -- Understand the question, Answer each sub-query (the SQL agent and
  its own steps in a dashed bracket) and Combine and explain -- cut from the
  step list itself by `pipelinePhases` in `src/pipelinePhases.js`. The five
  steps a model decides are raised rows with a round mark and a model chip;
  code steps are compact rows with a square mark, under a legend (asks a model,
  code, did not run, skipped; `stepStates`). Each step gives its name in plain
  words, its graph node name in mono (the name the Debug ledger uses) and one
  sentence on what it decides. While the list loads the page is a skeleton in
  its final shape. The steps come from `GET /api/pipeline`, which
  reads them from `nl2sql.pipeline.steps`; a test holds that list against the
  graphs themselves, so the page cannot describe a pipeline that is not the one
  running. Before a run each model step names the model it is set to use; after
  one it names the model that answered and shows that step's input, cached and
  output tokens, and every row gets a waterfall bar and its time (`waterfall`:
  the timings say how long, not when, so bars are laid end to end in pipeline
  order, nested steps inside the SQL agent's span, scaled to the run's total),
  joined to `usage.nodes` and `timings` by node name. `src/pipeline.js` and
  `src/pipelinePhases.js` do the joining and `npm test` covers them. The Debug
  drill-down is unchanged; this page is the overview, not a replacement.

- **Retrieval** (`#nav-retrieval` in the header nav, route `#/retrieval`; the
  page's content is `#retrieval-panel`): the Retrieval inspector. Text to embed
  (`#retrieval-query`), **Search** (`#retrieval-search`), picks `k`
  (`#retrieval-k`, the pool is shown as `4 * k`), lambda (`#retrieval-lambda`),
  a datasource filter (`#retrieval-datasource`) and one checkbox per entry type
  (`#retrieval-type-table`, `-column`, `-datasource`, `-join`, `-metric`). It
  posts to `POST /api/retrieval`; after the first search every knob searches
  again. The result (`#retrieval-result`) is the pool nearest first, with
  similarity (with a bar placed within the pool's own range, nearest full and
  farthest empty, since raw cosines bunch together), MMR pick order, the
  score each pick won with and its overlap with
  earlier picks, each entry's embedded text, and **Copy as text**
  (`#retrieval-copy`) for diffing two runs. The picks in order, and the
  entries passed over, sit above the table as rows of mono chips. A failure shows `#retrieval-error`.
  Where it is off (the same rule as Settings) it says why
  (`#retrieval-unavailable`), and the nav marks it.
- **Retrieval in the drill-down**: for `datasource_resolver` and
  `schema_retriever`, the node drill-down also shows the run's retrieval record
  from the trace: the text embedded, each search's pool with the picks marked,
  the entries MMR passed over, and the tables sent to the planner; or why no
  search ran.
- **Settings** (`#nav-settings` in the header nav, route `#/settings`; the
  page's content is `#settings-panel`): two raised cards, side by side at
  1061px and wider, and a skeleton of them while `GET /api/settings` loads.
  **API key** (`#settings-key`, a 44px mono input, and `#settings-save-key`) shows the key in use
  only in masked form (`#settings-key-current`), with three facts (Stored, Sent,
  Never) and the full text behind **How your key is handled** (`#settings-key-help`); saving one writes it to the
  demo project's `.env.demo` and turns replay into live without a restart, and
  the status pill follows. **Model for each step** has one selector per LLM node
  (`#model-datasourceresolver`, `#model-decomposer`, `#model-astplanner`,
  `#model-refiner`, `#model-answersynthesizer`) with a Default option, and
  **Save models** (`#settings-save-models`) writes the changed ones to
  `configs/llm.demo.yaml`.
  Choosing a model that runs without a temperature shows
  `#settings-temperature-note`. The model list comes from `GET /api/settings`;
  the page names no model itself. When the server has settings off (a
  non-loopback `--host` without `--allow-settings`) the page shows the reason
  (`#settings-unavailable`) instead of a form, and the nav marks it.

Every run station renders the `/api/ask` response; nothing is computed
server-side for the page.

## Rebuild

```bash
cd web/playground
npm install
npm test
npm run build
```

`npm run build` writes a single self-contained `index.html` (JS, CSS and fonts
inlined by `vite-plugin-singlefile`) into:

```
packages/nl2sql/src/nl2sql/cli/demo/playground/static/index.html
```

**That build output is committed on purpose.** The wheel has to contain the
finished page so `pip install "nl2sql-engine[demo]"` followed by `nl2sql demo`
works with no Node installed, and so CI never needs a JavaScript toolchain. The
trade is that a UI change is not live until you rebuild and commit the result --
if you edit anything under `src/`, run `npm run build` and commit
`static/index.html` in the same change.

`npm test` runs the pure helpers in `src/run.js` (SQL line breaks, the per-node
ledger, refused-table parsing, the trace drill-down helpers), `src/runState.js`
(each station's state, live and queued while busy, its head, the answer
header and its strip, the gate's timeline and reason, which error the fault
box explains, Ask turning into Stop, the elapsed counter, the chip row),
`src/faults.js` (the fault box's words and action for each provider error), `src/settings.js`
(model options, which nodes changed, which chosen models run without a
temperature), `src/indexHealth.js` (entry counts in plain words, the status
line, relative build times), `src/router.js` (which page a hash names, the nav
rows, and the router over `hashchange`), `src/home.js` (the home page's words,
its five steps, the sample question it asks and the facts it may claim),
`src/questions.js` (the guided
questions grouped by datasource), `src/firstRun.js` (what the hosted demo offers a
visitor with no key: the recorded questions, the badge, the key prompt on a
miss, and whose key answers once there is one),
`src/status.js` (what the top bar's status pill says in each mode, and the
sentence it stands for),
`src/datasources.js` (the
databases the switcher offers, whether it is a segmented control or a select,
and which one a run was answered from), `src/pipelinePhases.js` (the Pipeline
page's three phases, each step's state and the waterfall bars),
`src/railFade.js` (which edges of the rail fade), `src/hostedKey.js` (also the
key's masked tail, the provider cards and a key pasted under the wrong
provider), `src/indexHealth.js` (also the rail's one-line index summary and
when it opens itself),
`src/retrieval.js` (the MMR summary line, picks
in order, entries passed over, the copyable text form), `src/sqlHighlight.js`
(the SQL card's tokenizer), `src/artifacts.js` (the rows bar and the ledger and
pool bars, numeric columns, the CSV) and `src/feedback.js`
(when a run can be rated, the request body, the saved line) with Node's built-in test runner; there is no test dependency.

## Look

`src/styles.css` is the one stylesheet, built on tokens declared on `:root`
(light) and again under `prefers-color-scheme: dark`:

- **Colour** (Porcelain): `--paper`, `--surface`, `--raised`, `--well`, three
  inks, `--rule`/`--rule-strong`/`--hair`, one oxblood `--accent` used
  sparingly, `--accent-fill` (ink: the primary button), `--ok` for passed
  checks and a live status, `--warn`, and `--fault` for real faults. Every text
  colour holds 4.5:1 on every ground in both themes.
- **Type**: `--fs-1` to `--fs-6` (12 / 13.5 / 15 / 18 / 24 / 34px) and
  `--fw-regular` / `--fw-medium` / `--fw-bold` (400 / 500 / 650). Home's h1
  (`.home-title`, up to 54px) is the one other exception besides the answer
  sentence below. No other size
  is used, except the answer sentence on Ask: 28px (22px on a phone), so the
  answer is the largest text in a run.
- **Spacing**: `--sp-1` to `--sp-8` (4 / 8 / 12 / 16 / 24 / 32 / 48 / 72px).
- **One raised tier** (`--raised`, 12px radius, a `--hair` border, `--shadow`),
  and only for the composer, the answer header, the checks gate's body, the SQL
  and rows, and the node inspector. Everything else sits flat on the paper.
- **One button family**: primary (ink fill: Ask and Stop, Use this key,
  Replace the key, a needed Rebuild), secondary (outline: Rebuild, the rating buttons) and ghost
  (Close, Copy), 36px tall (44px on a coarse pointer), 8px radius. Hover
  changes colour only, inside `@media (hover: hover)`; a press scales to .98.
- Links share one style (accent, 1px underline at 3px offset), and every
  `<summary>` uses one CSS caret instead of the browser's triangle.

## Motion and touch

The tokens are on `:root`, once: `--ease-out` (`cubic-bezier(.2,.7,.2,1)`),
`--ease-in-out` (`cubic-bezier(.65,0,.35,1)`), and `--dur-press` 90ms,
`--dur-state` 160ms, `--dur-page` 180ms, `--dur-enter` 320ms, `--dur-data`
420ms. Only `transform` and `opacity` animate, and every animation sits inside
`@media (prefers-reduced-motion: no-preference)`; `src/styles.test.js` reads the
stylesheet and fails the build if either slips.

- **Route change**: `swapView` in `src/router.js` runs the page swap inside
  `document.startViewTransition` (React's `flushSync` puts the new page in the
  DOM inside the callback). `main` (`view-transition-name: page`) fades out
  and the new page fades in with a 4px rise over 180ms; the top bar has its
  own name, so it stays still. No API, reduced motion or a refused transition
  is an instant swap. Focus still moves to `#page` and the page starts at its
  top.
- **Nav**: one indicator (`.nav-ind`), a 1px bar moved and scaled to the
  current tab (`indicatorTransform`), slides between tabs over 240ms. Until it
  is placed the current tab's own accent border shows.
- **A result arriving**: the stations rise 40ms apart (`--n`), each mark fills
  just after its station, the row and ledger bars grow from `scaleX(0)` over
  420ms, 30ms apart (`--i`, the row), and the gate's tiles settle from scale
  .98.
- **Touch**: under `(pointer: coarse)` the nav, the chips, the rail's table
  rows, every button and the checkbox labels are at least 44px. The tap
  highlight is off for the whole page, and every hover rule is inside
  `(hover: hover)`.
- **Scroll**: `scroll-behavior: smooth` on `html` only under no-preference.
  After Ask, a phone brings the answer header up with `block: "nearest"`
  (`runScroll`), so the question box does not jump away.
- **Never**: bounce, parallax, a hover lift, glass or blur on content, glows,
  or animating width, height or top.

## Fonts and offline use

The page makes no network request other than its own API routes: no CDN, no
Google Fonts. The two typefaces are bundled at build time from `@fontsource`
(dev dependencies), Latin subset only, and inlined into the page:

- **Schibsted Grotesk** (variable) for the interface;
- **Fragment Mono** for plans, SQL, table and column names, and numbers.

Both are SIL Open Font License. The built page is about 350 KB (150 KB gzipped).

## The mark

The favicon is the plan spine: a teal root bar and stem with two indented ink
steps, a typed plan in outline. Its one copy is
[`docs/assets/favicon.svg`](../../docs/assets/favicon.svg), which follows
`prefers-color-scheme` (teal `#0c6a5c` and ink `#17201c` in light, `#62c7b1`
and `#e2e9e5` in dark). `index.html` inlines it as a `data:` URI, so the icon
costs no request either; the docs site names the file as `theme.favicon`, and
`scripts/social_card.html` draws the same shapes at card scale. If you change
the mark, change all three and run `npm run build`; a test in
`tests/unit/test_playground_app.py` fails if any of them, or the built page,
drifts from the file.

## Develop

```bash
npm run dev
```

Vite serves the page on its own port. Run `nl2sql demo --no-browser` alongside
it and proxy or point `fetch` at `http://127.0.0.1:8765` to exercise the real
API; the app calls `/api/meta`, `/api/schema`, `/api/ask`, `/api/trace/{id}`
the settings routes (`GET /api/settings`, `POST /api/settings/key`,
`POST /api/settings/models`) and the index routes (`GET /api/index`,
`POST /api/index/rebuild`), the inspector's (`GET /api/retrieval`,
`POST /api/retrieval`) and feedback's (`GET /api/feedback`, `POST /api/feedback`).
The settings routes, Rebuild, the inspector and feedback refuse a request
whose `Origin` is not the page's own, so use them from the page `nl2sql demo`
serves, not from the Vite dev server.

## Scope

React and Vite only -- no router library (`src/router.js` is 144 lines over the
hash), no state library, no component kit, no CSS framework, no TypeScript. Plain JSX and plain CSS, kept small enough to read in
one sitting. Light and dark follow `prefers-color-scheme`; motion is the page
crossfade, the nav indicator and the run arriving in order, and is off under
`prefers-reduced-motion` (see Motion and touch). The only
browser storage is the Debug toggle; settings live in the demo project's files,
and ratings in its schema store.
