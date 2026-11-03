# Key Design Decisions

The choices that shape the system, each with the alternative it was chosen over and the reason, for contributors and for anyone judging whether the design fits their use.

## A typed plan over generated SQL

We chose a typed plan the model fills in over SQL the model writes. A plan can be validated against the schema, the caller's role and a function allow-list before any SQL exists, and it renders the same on every database. Free SQL can only be checked by parsing it afterwards. See [Plans and SQL](plans-and-sql.md).

## Validate before generate

We chose to make the logical validator reject everything the generator could reject, over letting the generator fail late. The validator is the last node with a retry edge, so a problem found there is a retry with a reason, not a dead end. See [Logical Validator](nodes/logical_validator_node.md).

## The adapter owns the dialect

We chose to keep every dialect name and dialect-specific rule inside adapters, over teaching the planner each database. The plan says what; the adapter says how. A new database is a new adapter and no change to the engine. See [Packages and boundaries](packages-and-boundaries.md).

## A facade every app shares

We chose one `NL2SQL` facade that the API, the CLI and the MCP server all use, over each app reaching into the runtime. A capability that exists only in one app cannot be used or tested by the others, so anything an app needs becomes a facade method. A test enforces it.

## The answer model sees a preview, not the result

We chose to give the answer model a bounded preview (the first rows, the column names and the row count) over the full result, so the prompt does not grow with the result. The conversation model sees no rows at all, only each answer's summary and columns. Conversations and dashboards store plans and summaries, not rows.

## Strict, fail-closed access

We chose to refuse a plan that touches a table the role may not read, over answering from the tables the role can see. A partial answer looks complete and is wrong in a way nobody notices. An unknown role grants nothing, and a failing policy check denies. See [Access and security](access-and-security.md).

## Ownership by key, not by permission table

We chose to key every user-made record by `(tenant_id, user_id)` and to answer "not found" to anyone else, over a separate permissions system. A new record type is private by default, and an id reveals nothing about whether it exists.

## Plans in dashboards, re-checked per viewer

We chose to store the plan behind each chart and re-run it under the viewer's scope, over caching rows or SQL. Refresh costs no model call, a changed schema surfaces as a chart that needs fixing, and a dashboard shared with someone can never widen their access. See [Dashboards and saved answers](dashboards-and-saved-answers.md).

## Model-led chat over a fixed dialogue

We chose to let the model decide, step by step, whether to answer or to ask the database, over a scripted flow. Every question it asks still goes through the same pipeline and checks as one typed by hand. See [Conversations](conversations.md).

## Configuration in an object, not the environment

We chose a `Config` passed to the engine and an allow-list for environment reads, over process-wide settings. Two engines in one process share nothing, and only the entry points load `.env`. See [Configuration loading](configuration-loading.md).

## One storage folder, pluggable stores

We chose a single `storage.path` with a slot per kind of data and stores named by entry points, over scattered locations. An installation is one folder to back up or delete, and a different vector store or results bucket is a plugin. See [Jobs and storage](jobs-and-storage.md).

## Jobs in process

We chose an in-memory queue with one worker thread over an external job system. The target is a single-container deployment, a restart loses the job list and stops a running job, but what finished jobs produced is in the engine's stores.

## Ask when the reading is ambiguous

We chose to ask which reading of an ambiguous question was meant, over picking the likeliest one silently. A wrong answer that looks right costs more than one extra click. See [Clarifier](nodes/clarifier_node.md).

## Verified answers before generated ones

We chose to check for a trusted, parameterised query the data team wrote before asking the model to plan, over always generating. A verified answer is exact and cheap, and a person reviews every change to the set. See [Curated Answers](curated_answers.md).

## Determinism where it is cheap

We chose stable ids, sorted layers, a total row order and a plan cache over pretending the model is reproducible. Model output varies; everything around it does not. See [Determinism](determinism.md).
