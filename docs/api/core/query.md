# Query API

## Purpose
Answer a question with the NL2SQL pipeline: `engine.run_query`,
`engine.stream_query`, and `engine.results` to read back what a run saved.

## Responsibilities
- Invoke pipeline graph runtime.
- Return a structured `QueryResult`, or the run as events.
- Read a run's saved results by its `trace_id`.

## Key Modules
- `packages/nl2sql/src/nl2sql/public_api.py` (`NL2SQL.run_query`, `NL2SQL.stream_query`)
- `packages/nl2sql/src/nl2sql/api/query_api.py` (`QueryResult`, `result_from_state`)
- `packages/nl2sql/src/nl2sql/api/results_api.py` (`ResultsAPI`, internal)
- `packages/nl2sql/src/nl2sql/pipeline/runtime.py`
- `packages/nl2sql/src/nl2sql/pipeline/graph.py`
- `packages/nl2sql/src/nl2sql/pipeline/state.py`

## Public Surface

### NL2SQL.run_query

Source:
`packages/nl2sql/src/nl2sql/public_api.py`, `packages/nl2sql/src/nl2sql/api/query_api.py`

Signature:
`run_query(question: str, *, datasource_id: Optional[str] = None, execute: bool = True, user_context: Optional[UserContext] = None, cancellation_token: Optional[CancellationToken] = None, plan: Optional[SuppliedPlan] = None, approvals: Optional[Sequence[Approval]] = None, clarify: bool = True, choice: Optional[ClarificationChoice] = None, answer: bool = True) -> QueryResult`

Every argument after the question is keyword-only. `run_query` passes the runtime's artifact store to `result_from_state`, so each
sub-query carries a capped row sample alongside its artifact reference.

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `question` | `str` | yes | The question, in plain language. |
| `datasource_id` | `Optional[str]` | no | Datasource override; otherwise resolved. |
| `execute` | `bool` | no | Whether to execute SQL against datasource. |
| `user_context` | `Optional[UserContext]` | no | RBAC context. Omitted (or `None`), the run starts with an empty `UserContext`, a caller with no roles, and RBAC treats it exactly as it treats any roles list it has no policy for. Pass one with a role to be granted anything. |
| `cancellation_token` | `Optional[CancellationToken]` | no | A token (`from nl2sql import CancellationToken`) the caller cancels from another thread to stop the run. No further step and no further model call starts once it is cancelled; the step in flight finishes, and the result carries one `CANCELLED` error. The playground cancels it when the browser drops the request. |
| `plan` | `Optional[SuppliedPlan]` | no | A plan the caller holds (`from nl2sql import SuppliedPlan`): a whole run (`decomposition` and `plans`), or a verified query (`verified` and `verified_datasource_id`). See [Running a plan you hold](#running-a-plan-you-hold). A `datasource_id` passed with it must be the datasource of every sub-query of the plan, or the call raises `ValueError` before anything runs. |
| `approvals` | `Optional[Sequence[Approval]]` | no | Approvals for an earlier run's `approvals_needed`. See [Approval](#approval). |
| `clarify` | `bool` | no | Default `True`: a question the planner reads two or three ways ends `needs_clarification` with the options instead of running the most likely one. `False` runs the most likely reading and lists the others as assumptions. The prompts are the same either way. See [Clarification](#clarification). |
| `choice` | `Optional[ClarificationChoice]` | no | The caller's pick from an earlier run's `clarification` (`from nl2sql import ClarificationChoice`: `request`, `option_id`). Ask the same question as the same caller: the chosen option's plan runs through the validator and the generator without routing, decomposing or planning. See [Asking again](#asking-again). Not with `plan` or `approvals`. |
| `answer` | `bool` | no | Default `True`. `False` skips the answer synthesizer: the run ends at the aggregator, `final_answer` is `None` and no model is called to write prose. With `plan=` that is a run with no model call at all, which is how a dashboard refresh re-runs a stored plan. The rows, the SQL and the explanation are the same. See [Running without an answer](#running-without-an-answer). |

#### Running a plan you hold

`run_query(question, plan=SuppliedPlan(...))` runs a plan without routing or
planning: the curated matcher, the datasource resolver, the decomposer and
the planner's model call are skipped. A `SuppliedPlan` is a whole run, shaped
on the pinned run the approval re-run uses, and is exactly one of:

- `decomposition` (a `DecomposerResponse`, its combine groups and post-combine
  steps included) and `plans`, the plan of **every** sub-query keyed by its
  id; or
- `verified` (a `VerifiedRef`: the verified query's id and its bound `params`)
  and `verified_datasource_id`.

`SuppliedPlan.for_sub_query(sub_query, plan)` is the common case, one
sub-query run standalone. A plan missing for a sub-query, a plan for a
sub-query that is not there, or both forms at once is a `ValidationError`
when the `SuppliedPlan` is built. What is not skipped:

- Every datasource of the decomposition is checked against `user_context`'s
  scope, as the resolver would; a datasource the caller may not read ends the
  run with `SECURITY_VIOLATION` before anything runs.
- The plan passes the `plan.after` hooks, the logical validator (so a table
  outside the caller's scope is refused) and the generator, like any plan.
- It is never written to the plan cache, and a validator failure ends the
  sub-query with the validator's error: the refiner is never asked for a
  different plan. A database error is not retried either.

The sub-query's `plan_source` is `"supplied"`. The question is still asked:
the answer is written for it (unless `answer=False`). The same entry serves
the approved re-run (`"approved"`) and, in Wave B, a plan the asker chose
(`"chosen"`).

A supplied verified query is run by the curated matcher with its values, and
it never falls back to a model run (a dashboard refresh uses no tokens). The
call raises `ValueError`, before anything runs, when no curated file with a
verified query is loaded, or `datasource_id` is not `verified_datasource_id`.
With `execute=False` the matcher checks the query (the caller's scope, its
values, every table it reads) and runs nothing: the run ends `plan_only`, or
with the error below (a dashboard's access check). Where an approved pin would fall back to
generation, a supplied one ends the run with an error and makes no model call:
`SECURITY_VIOLATION` (the role may not read the datasource or a table the query
reads), `EXECUTION_FAILED` (the query failed) or `INVALID_STATE` (an unknown
query id, or a value that is missing or out of bounds).

#### Running without an answer

`run_query(question, plan=plan, answer=False, user_context=...)` is a run with
no model call. It is how a stored plan is re-run to refresh a chart: the graph
is built without the answer synthesizer, so the run ends at the aggregator and
`final_answer` is `None` (a stopped or timed-out run too: it has no stop text),
`timings` has no `answer_synthesizer` and a streamed run emits no
`answer.delta`. `answer` is recorded in the trace's `request`, so a replay runs
the same graph. On its own, `answer=False` still routes, decomposes and plans
with the models; pair it with `plan=` for a run that calls none.

Hooks still apply. While a hook is attached at `answer.after`, a model-free step
runs it with `final_answer=None`, so a `Deny` withholds the rows and the result
manifest as it does after an answer, and `stream_query` sends the
`result.manifest` event only once the hook has allowed it.

#### Approval

A hook at `execute.before` may answer `NeedsApproval(reason)`. The run then
ends with `status: "needs_approval"`, the error `NEEDS_APPROVAL` and one
`ApprovalRequest` per pending sub-query in `approvals_needed`: the question,
the asker's `user_id` and `tenant_id`, the hook, the datasource, the run's
decomposition, the sub-query and its plan (or, on the verified path, the
verified query's id and values), the SQL and its SHA-256, the reason, and the
roles that may approve (for display). Its `id` is a SHA-256 over a fixed list
of those fields (`REQUEST_ID_FIELDS`: all but the reason and the roles), each
without the values it holds at their defaults, so an edit shows, the same
request always has the same id, and a field added in a later version keeps
the ids of requests an app stored before.

An approver re-runs the question as the asker:

```python
result = engine.run_query(question, user_context=asker)
if result.status == "needs_approval":
    approvals = [Approval(request=r, by=approver) for r in result.approvals_needed]
    result = engine.run_query(question, user_context=asker, approvals=approvals)
```

The engine, not the hook, checks each approval. An approval pins nothing when
its request was edited (its `id` no longer matches), its `sql` is not the SQL
of its `sql_sha256` (the SQL an approver reads is the SQL whose hash is
checked), it is for another question or another asker, its hook is not
configured, or `by` holds none of that hook's approver roles. Then the run
plans as usual and the hook asks again. Otherwise the re-run takes the
request's decomposition and plan (`plan_source: "approved"`, no resolver,
decomposer or planner call) and validates and generates the SQL again. At `execute.before` the approval
counts as the hook's `Allow` only when it is for that hook and datasource,
the hash of the SQL just generated equals the request's (and, on the verified
path, the values are the same), and `by` holds one of the hook's configured
`approver_roles` (by default `policies.admin_roles`), never the roles the
request lists. Then the next hook still runs. Anything else asks again: the
run ends `needs_approval` with a new request. Nothing is stored, and an
`Approval` is not signed and does not expire: it is trusted as far as the
`user_context` a caller passes. See [Hooks](../../configuration/hooks.md#approval).

Returns:
`QueryResult`, built from the pipeline graph state by `result_from_state`.

Raises:
No exceptions are raised by `run_query`; errors are returned in `QueryResult.errors`.

Side Effects:
- Pipeline execution, LLM calls, optional database execution.

Idempotency:
- Not guaranteed; execution can depend on external systems and time.

## Execution Lifecycle
- Use the caller's `CancellationToken`, or create one per run, and install signal / Ctrl+X handlers bound to it.
- Pass the graph a callback that raises `RunCancelled` at the next node start or model call once the token is cancelled.
- Build LangGraph pipeline from `build_graph`.
- Execute graph in thread pool with timeout.
- On timeout/cancel, return `PipelineError` with appropriate `ErrorCode`.

### QueryResult

Source:
`packages/nl2sql/src/nl2sql/api/query_api.py`

Fields:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `sub_queries` | `List[SubQueryResult]` | no | One entry per decomposed sub-query. |
| `final_answer` | `Optional[Dict[str, Any]]` | no | Answer synthesizer payload (`summary`, `format_type`, `content`, `warnings`, and `truncated`: True when a result was cut at the row limit). |
| `errors` | `List[Dict[str, Any]]` | no | Pipeline errors (`node`, `message`, `error_code`, `severity`, `provider`, `provider_response`). `provider` and `provider_response` are set for a `PROVIDER_*` code and `null` otherwise; see [provider failures](../../observability/error-handling.md#provider-failures). |
| `trace_id` | `str` | no | Trace identifier. |
| `reasoning` | `List[Dict[str, Any]]` | no | Reasoning events/logs. |
| `warnings` | `List[RunWarning]` | no | What the answer should say without failing the run. See [Warnings](#warnings). |
| `artifact_refs` | `Dict[str, ArtifactRef]` | no | Result artifact references keyed by execution node id. |
| `status` | `Optional[RunStatus]` | no | `"success"`, `"plan_only"`, `"error"`, `"needs_approval"`, `"stopped"`, `"needs_clarification"`, or `None` when nothing ran and nothing failed. See below. |
| `timings` | `Dict[str, float]` | no | Wall-clock seconds per graph node, plus `LangGraph` for the whole run. |
| `usage` | `QuestionUsage` | no | LLM calls, tokens and model time per node and for the whole question. See below. |
| `trace_path` | `Optional[str]` | no | Where this run's trace file was written, or `None` when `TRACE_MODE` did not write one. See [Debugging a Run](../../observability/debugging.md). |
| `truncated` | `bool` | no | True when any sub-query's result reached its row limit, so rows may be missing. See `sub_queries[].truncated`. |
| `result_manifest` | `List[ResultSummary]` | no | The run's saved results, one per terminal DAG node (`node_id`, `columns`, `row_count`, `truncated`, `column_kinds`): the manifest the aggregator writes beside them. `column_kinds` maps a column to its type category (`number`, `text`, `temporal`, `boolean`, `other`); the aggregator fills it from the saved file's column types, so `engine.results.chart` can choose a chart without reading rows. Never the rows; see [Reading a run's saved results](#reading-a-runs-saved-results). Empty when nothing was saved. |
| `source` | `AnswerSource` | no | Where the answer came from: `type` `"verified"` with the verified query's `id`, `datasource_id` and the `params` it ran with, or `type` `"generated"` with the other fields `None`. See [Curated answers](../../configuration/curated.md#what-happens-at-question-time). |
| `stop` | `Optional[RunStop]` | no | Set when `status` is `"stopped"`: the `limit` the run reached (`max_llm_calls` or `max_cost_usd`), its configured `value`, and what was `spent`. |
| `approvals_needed` | `List[ApprovalRequest]` | no | Set when `status` is `"needs_approval"`: one request per sub-query a hook asked a person to approve, with the SQL, its SHA-256, the reason and the asker. Re-run the question with an `Approval` for each. |
| `clarification` | `Optional[ClarificationRequest]` | no | Set when `status` is `"needs_clarification"`: the phrase, and two or three readings, each a label, a validated plan and its explanation. See [Clarification](#clarification). |
| `explanation` | `Optional[Explanation]` | no | The run's plan in plain English, rendered from the typed plans with no model call: one step per sub-query with its tables, joins, filters, groupings, measures, order, glossary terms, rules and assumptions; a verified answer is one step naming the verified query. Only steps that passed validation are explained (a `success` or `plan_only` run's, and the successful steps of a partly failed one); `None` when no step did, when a hook ended the run, or when it cannot be rendered. See [Explanation](#explanation). |
| `decomposition` | `Optional[DecomposerResponse]` | no | The run's whole decomposition (sub-queries and combine groups), set on every run that has one and withheld, like the data, when a hook ended the run. With each sub-query's `plan` it rebuilds the run: a dashboard chart stores both ([Wave C](../../architecture/wave_c.md)). |

`errors` holds the `ERROR` and `CRITICAL` entries, and the one `INFO` entry
`NEEDS_CLARIFICATION` of a run that asked which reading was meant;
`WARNING`-severity pipeline errors are appended to `warnings` as `RunWarning`s.

A sub-query that succeeded contributes nothing to `errors`: the errors of the
attempts it recovered from are in `warnings` with `detail.recovered` true, and
its warnings with an empty `detail`, each with its `sub_query_id`. The run trace and
`subgraph_outputs[<id>].errors` keep every attempt's errors for debugging. A
sub-query that failed without a blocking error of its own (it ended with no SQL
on warnings alone) adds a `MISSING_SQL` error, so the run reports `"error"`.

`status` is derived: `"needs_approval"` when a hook asked for approval
(`approvals_needed`), `"stopped"` when the run reached a per-question limit
(`stop`), `"needs_clarification"` when the run asked which reading was meant
(`clarification`), otherwise `"error"` when any blocking error is present, otherwise
`"plan_only"` when no sub-query produced rows or an artifact (an
`execute=False` run), otherwise `"success"`. A state with no sub-queries and no
errors leaves it `None`. Hooks, approval and the caps
that produce `"needs_approval"` and `"stopped"` arrive with release 0.7 (see
[Wave A](../../architecture/wave_a.md)).

Only a capped sample of the rows is inlined, in `sub_queries[].rows`. The full
result set lives in artifact storage, addressed through `artifact_refs`; each
combined result is listed in `result_manifest` and read back by the run's
`trace_id` with the methods below.

### Clarification

When the planner reads a phrase of the question in more than one way that
changes the result ("sales": the invoice total or the invoiced amount), it
names the phrase and gives up to two other readings beside its most likely
one. The `clarifier` step, after the logical validator, runs each other
reading through the validator's own checks with the caller's scope and drops
any that fail, so an option never names a table the caller cannot read, then
merges readings that render to the same SQL. When two or three readings
remain, the question has exactly one sub-query, `clarify` is on and
`settings.clarify.enabled` is on (the default), the run ends before the
generator: nothing is executed, aggregated or synthesized.

```python
result = engine.run_query("What were sales last year?", user_context=ana)
if result.status == "needs_clarification":
    for option in result.clarification.options:
        print(option.id, option.label, option.explanation.text)
```

The result then has:

- `status` `"needs_clarification"` and one `errors` entry,
  `NEEDS_CLARIFICATION` (`INFO`, never retried);
- `final_answer` a fixed sentence: "This question can be read more than one
  way. Choose one: Sales: total of invoice total; Invoiced amount.";
- `clarification`, a `ClarificationRequest` (`from nl2sql import
  ClarificationRequest`): `id` (SHA-256 of every other field, 32 hex, so a
  mixed-up or edited request is recognised), `question` (as passed, before any
  `run.before` hook), `asker_user_id`, `asker_tenant_id`, `phrase`,
  `datasource_id`, `schema_version`, `knowledge_digest`, `sub_query` and
  `options`;
- each option a `ClarificationOption`: `id` (`"a"` is the most likely
  reading, then `"b"`, `"c"`), `label`, `plan` (the plan as JSON) and
  `explanation` (an `ExplainedStep`, as in [Explanation](#explanation)). The
  planner gives no label for its most likely reading, so `a` is named by the
  glossary term its plan reads, else its first measure, filter or table;
- `explanation` `None`, and the sub-query's `status` `"needs_clarification"`
  with no SQL: nothing ran, so the options carry the explanations.

Otherwise the most likely reading runs, and the readings that passed the
checks are listed as assumptions in its explanation step ("'sales' was read
one way; it could also mean Invoiced amount."). That happens with
`clarify=False`, with more than one sub-query, when only one reading
passed, and on a re-plan after an attempt already executed (a sanity check's
retry), so the answer is never the flagged attempt's rows. `clarification.requested` (`sub_query_id`, `phrase`, `options`) is
published on the engine's bus when a run asks; it is an operator event, not
on the stream. Evals run with clarification off, so a case measures the most
likely reading; a case may expect `clarify`, and the
[clarify suite](../../evals/cases.md#the-clarify-suite) does.

### Asking again

```python
choice = ClarificationChoice(request=result.clarification, option_id="b")
answer = engine.run_query("What were sales last year?", user_context=ana, choice=choice)
```

The re-ask is stateless: the caller holds the request and sends it back, and
nothing is stored. The engine checks, before anything runs:

| Check | Failure |
| --- | --- |
| The request's `id` is the hash of its content; its `question` is the question as passed now (before any `run.before` hook); its asker is this caller; `option_id` is one of its options | `CLARIFICATION_INVALID` (`CRITICAL`). The id is an unkeyed hash, a check against mixed-up requests, not tamper evidence: the validator, run on the chosen plan with the caller's scope, is the guard against an edited plan |
| The datasource is in the caller's scope, the request's `schema_version` is the live one and its `knowledge_digest` the live glossary's | `CLARIFICATION_STALE` (`ERROR`, never retried): the data or the glossary changed since the options were offered; ask again |
| No `approvals` come with it | `CLARIFICATION_INVALID`: pass the approval alone |

A refused choice ends the run with that one error (`status` `"error"`) and
calls no model. A `plan` or a `pinned` passed with a `choice` is a `ValueError`
at the call. Otherwise the run enters at Wave A's pinned entry with one
sub-query, standalone: no resolver, decomposer or `datasources.after`. The
planner step returns the chosen plan with `plan_source` `"chosen"` and no model
call; then it is an ordinary run: `plan.after`, the validator with the
caller's scope, the generator, `execute.before`. A chosen plan is one person's
reading and is never cached. The sub-query's `plan_source` is `"chosen"` and
its explanation says "You chose: Invoiced amount".

If a hook (the cost guard, say) answers `needs_approval` on the chosen plan,
the run ends `needs_approval` with an `ApprovalRequest` for the chosen plan;
the approver's re-run passes `approvals` only.

`engine.knowledge.save_choice(choice, user_context=ana)` files the pick as a
`term` proposal, so an admin can make the phrase part of the glossary: see
[the review queue](../../configuration/review.md#saving-a-clarification-pick).

### Warnings

`QueryResult.warnings` is a list of `RunWarning` (`from nl2sql import RunWarning`):

| name | type | meaning |
| --- | --- | --- |
| `code` | `str` | An `ErrorCode` value, such as `VALUE_NOT_FOUND`, `JOIN_FAN_OUT`, `ALL_NULL_COLUMN`, `JOIN_KEY_UNCONVERTED`, `RESULT_TRUNCATED` or `HOOK_DENIED`; `CURATED_FALLBACK` when a verified query failed and the question was generated instead. |
| `node` | `str` | The node that found it. |
| `message` | `str` | What the answer says. |
| `sub_query_id` | `Optional[str]` | The sub-query it is about, when it is about one. |
| `detail` | `Dict[str, Any]` | What a program may branch on: `recovered: true` for an earlier attempt's error a retry replaced; the column, counts and type of a `JOIN_KEY_UNCONVERTED`. Never result rows or sample values. |

The answer sanity checks (a missing filter value, a join that repeated the rows
an aggregate counted, a column with no value) add theirs, and the answer
synthesizer says them; see the
[executor's sanity checks](../../architecture/nodes/executor_node.md#answer-sanity-checks).
`settings.pipeline.sanity_checks: false` turns those checks off.

0.7 changed this shape: a warning was a dict with `error_code` and `severity`,
and is now a `RunWarning` with `code`, and `detail.recovered` where `severity`
said `ERROR`. A run's state is never kept across an upgrade (a run that ended
`needs_approval` is asked again, not resumed; see
[Wave A, D10](../../architecture/wave_a.md#7-approval)), so no older warning is
ever read back.

### Explanation

`QueryResult.explanation` answers "why this answer" for a business reader. It
is rendered by `nl2sql/explain/` when the result is assembled
(`result_from_state`), from each sub-query's typed plan, the knowledge its
prompt carried (`sub_queries[].knowledge_used`), the planner's readings it did
not ask about, the row cap and the run's warnings. No model is called, and the
same plan and knowledge always give the same words. The answer synthesizer is
not given it: the explanation describes the query, the answer describes the
rows.

Only a sub-query that passed validation is explained. A plan the validator
or a hook refused, and a dropped sub-query, get no step: their tables, and
their glossary descriptions, would confirm what the refusal withholds. A run a
hook ended (`HOOK_DENIED`, `HOOK_FAILED`, `NEEDS_APPROVAL`, `COST_LIMIT_EXCEEDED`) has no
explanation, as it has no rows. An explanation never fails a run: one that
cannot be rendered (a stored plan an older version wrote, say) is `None`, and
the error is logged.

The glossary that names tables and tells a glossary join from a declared key
is the caller's: every caller of `result_from_state` (`run_query`,
`stream_query`, evals, trace replay and the trace itself) passes the
knowledge filtered by the caller's scope (`caller_knowledge` in
`nl2sql/knowledge/scope.py`), so a description that mentions a table the
caller cannot read never reaches the explanation. It is the knowledge and
snapshot the run pinned (`run_with_graph` hands back its pinned runtime as
`pinned_runtime`), so a reload during the run does not change the words.

`Explanation` has `steps` (one `ExplainedStep` per sub-query, in the
decomposition's order) and `combined`: how the steps' results were put
together ("The results of a and b were joined on country."), or `None` for a
single step. An `ExplainedStep`:

| name | type | meaning |
| --- | --- | --- |
| `sub_query_id` | `Optional[str]` | The sub-query it explains. |
| `datasource_id` | `str` | Its datasource. |
| `source` | `str` | Where the plan came from: `generated` (the planner), `cache`, `supplied` (`run_query(plan=...)`), `approved`, `chosen` (a clarification pick) or `verified` (a verified query, with no plan). |
| `tables` | `List[str]` | Each table, qualified as the plan names it, with its glossary description's first clause when there is one: `"Invoice: one row per customer invoice (glossary)"`. |
| `joins` | `List[str]` | Each join and the relationship it used: `"each InvoiceLine to its Invoice, on InvoiceId (declared key)"`, or `(glossary)` for a join the glossary declares. |
| `filters` | `List[str]` | Each condition of the `WHERE`, then each of the `HAVING` as `"for each group, ..."`. |
| `groupings` | `List[str]` | `"by Customer country"`. |
| `measures` | `List[str]` | Each aggregate the plan selects, by its alias: `"revenue: total of InvoiceLine unit price times InvoiceLine quantity"`. |
| `order` | `Optional[str]` | `"highest revenue first, top 10"`. |
| `terms` | `List[TermUse]` | The glossary terms sent with the step whose columns the plan reads: `term`, `definition`, `source`. |
| `rules` | `List[str]` | The business rules sent with the step, by text. |
| `assumptions` | `List[str]` | What the answer took for granted, all deterministic: a reading not chosen when an ambiguity was not asked about; a filter value the column did not have (`'Brasil' is not in Customer country; 'Brazil' was used instead.` when the final plan filters on a suggested value, else `...; the question was planned again.`); the sanity checks' warnings (`VALUE_NOT_FOUND`, `JOIN_FAN_OUT`, `ALL_NULL_COLUMN`, `JOIN_KEY_UNCONVERTED`), and the row cap when the result was cut. |
| `planner_note` | `Optional[str]` | The plan's own `reasoning`: model text, kept apart and labelled as the planner's. It appears in no other field. |
| `text` | `str` | The step in two to four sentences: what it shows, what it reads and filters, which groups it keeps, and its order. |

Names are the glossary description's first clause when there is one, else the
name split into words (`InvoiceDate` on `Invoice` is "Invoice date"); a table
the plan reads twice is named with its alias ("Track (t3)"). Each expression
kind has a phrase (`=` "is", `IN` "is one of", `LIKE` "matches", `IS NULL` "is
empty", `SUM` "total of", `COUNT` with `distinct` "number of different",
`DATE_PART('year', d)` "the year of d", a `case` "depending on: ..."); a
function without one is written as its name and arguments.

`result_from_state(state, ..., knowledge=None)` takes `knowledge`, a mapping
from datasource id to its glossary (`DatasourceKnowledge`) as the caller's
scope filters it; without it, names are the split names and every join is a
declared key. A run on the Chinook
suite's gold plans renders as the files in
`packages/nl2sql/tests/unit/explain/golden/`, which are compared on every
test run, so a change to the wording is a reviewed diff. Design:
[Wave B, section 8](../../architecture/wave_b.md#8-why-this-answer).

## Streaming a run

### NL2SQL.stream_query

Source:
`packages/nl2sql/src/nl2sql/api/query_api.py`, step events in
`packages/nl2sql/src/nl2sql/pipeline/events.py`, the allow-list in
`packages/nl2sql/src/nl2sql/events/catalog.py`

Signature:
`stream_query(question: str, *, datasource_id: Optional[str] = None, execute: bool = True, user_context: Optional[UserContext] = None, cancellation_token: Optional[CancellationToken] = None, plan: Optional[SuppliedPlan] = None, approvals: Optional[Sequence[Approval]] = None, clarify: bool = True, choice: Optional[ClarificationChoice] = None, answer: bool = True) -> Iterator[Event]`

The same run as `run_query`, with the same parameters, yielded as it happens:
the run's own events on the engine's bus ([Events](../../observability/events.md)),
from the caller-safe allow-list. `nl2sql run` shows its live progress from them.

```python
from nl2sql import NL2SQL, UserContext

engine = NL2SQL.from_config("nl2sql.yaml")
for event in engine.stream_query("Which genre sells the most tracks?",
                                 user_context=UserContext(roles=["admin"])):
    if event.name == "node.finished":
        print(f"{event.data['node']} took {event.data['seconds']} s")
    elif event.name == "answer.delta" and event.data["field"] == "summary":
        print(event.data["text"], end="", flush=True)
    elif event.name == "run.finished":
        result = event.data["result"]   # run_query's QueryResult, as JSON
```

An `Event` (`nl2sql.Event`, from `nl2sql_sdk.events`) has a `name`, `at` (UTC),
the run's `trace_id`, its `tenant_id` and a JSON-ready `data` dict;
`event.sse()` is the event as one Server-Sent Event (`event: <name>`, and the
whole event as JSON on the `data` line), which is how `nl2sql-api` and the
playground send it.

| name | data | when |
| --- | --- | --- |
| `run.started` | `question`, `user_id`, `tenant_id`, `roles`, `datasource_id`, `execute` | The run began. Always first. |
| `node.started` | `node`, `sub_query_id` | A pipeline step began (the SQL agent's steps once per sub-query, and again on a retry). `sub_query_id` is None outside a sub-query. |
| `node.finished` | `node`, `sub_query_id`, `seconds`, `ok` | The step ended; `ok` is False when it raised. |
| `result.manifest` | `results` | The aggregator saved the results: `results` is the run's `result_manifest` and the event's `trace_id` its `request_id`, so `engine.results.page` can be called before the answer is written. |
| `answer.delta` | `field`, `text` | The next piece of `final_answer.summary` or `final_answer.content`, as the answer model writes it. |
| `sanity.flagged`, `run.limit_reached` | see [Events](../../observability/events.md) | Published by the sanity checks and the caps when they arrive (Wave A's S1 and E3). |
| `run.finished` | the run's summary (`status`, `duration_s`, `usage`, `error_codes`, `source`, `datasources`, `tables`, `result_manifest`, `user_id`, `roles`) plus `result` | The run finished. `result` is exactly what `run_query` returns, as JSON; it is on the stream's copy only, never on the bus. A refusal, a provider failure, a timeout or a cancel is a `run.finished` with `status: "error"`, as `run_query` returns it. |
| `run.failed` | `message`, `error_code` | The run could not start or finish (an exception outside the pipeline, such as a missing API key when the graph is built). |

Every stream ends in exactly one `run.finished` or `run.failed`. Operator
events (`policy.decision`, `hook.decision`, `llm.call`, `plan_cache.lookup`)
are never on it: a denial would tell a denied caller which table exists, and the
rest are operator data. An operator reads them with `engine.events.subscribe`.

How it works:

- The run goes on a thread of its own, started on the first `next()`; the caller's
  context goes with it, so request-scoped keys (`nl2sql.llm.request_key`) reach the
  graph as they do in `run_query`.
- Every run (streamed or not) has a `RunScope`, and `QueryAPI.open_scope` attaches
  a `RunEvents` to it: a LangChain callback the graph gets with the scope's
  callbacks, which publishes through the scope. A node's own run (its name is its
  `langgraph_node`) is a `node.started` / `node.finished`; the runnables inside a
  node are not events. The aggregator's end publishes `result.manifest`. The
  stream subscribes to its scope's events from the allow-list
  (`nl2sql.events.STREAM_EVENTS`), so it yields exactly what the bus carries, in
  publish order, and then its own `run.finished` with the result.
- `run_with_graph(stream_answer=True)` asks the answer synthesizer to stream: its
  chain is `streaming_structured` (`nl2sql.llm.wires`) instead of `structured`, the
  same request with `stream` on. Each piece of the answer's tool call reaches
  `on_llm_new_token`; `RunEvents` parses the arguments so far as partial JSON and
  publishes what `summary` and `content` gained as `answer.delta`. The pieces are joined into one message
  before the parser sees it, so the answer, and the token usage the call reports,
  are the unstreamed call's. On the Anthropic wire the joined message gets the same
  tool-call repair as an unstreamed one.
- Closing the iterator before the last event (a client that went away) cancels the
  run's token, so it stops before its next step or model call.

Token by token, per wire: both the OpenAI wire (OpenAI, OpenRouter, Ollama, any
OpenAI-compatible endpoint) and the Anthropic wire stream the answer's forced tool
call, so the answer streams on every provider; how finely is the provider's choice,
and one that sends the whole answer at once gives one `answer.delta` per field. The
other model steps return a typed object the next step needs whole, and are not
streamed. `FakeLLMServer` streams both wires, a word at a time, so replay mode and
the keyless tests stream too.

## Reading a run's saved results

The aggregator saves each terminal result of a run as
`<tenant_id>/<request_id>/result_<node_id>.parquet` and writes `manifest.json`
beside them (see [Artifact Store → The result manifest](../../storage/artifact-store.md#the-result-manifest)).
While a hook is attached at `answer.after`, the manifest is written only once
those hooks allowed, so a run's results are not readable before that, and never
for a run a hook ended ([What withholding guarantees](../../configuration/hooks.md#what-withholding-guarantees)).
`engine.results` reads them back by the run's `trace_id` (its `run_id`, the
`request_id` of the REST routes). They read lazily from the
parquet file: a page reads only its slice, and the CSV export reads and yields a
slice at a time (`CSV_CHUNK_ROWS`, 10,000 rows), so neither holds the whole
result in memory.

| method | returns | meaning |
| --- | --- | --- |
| `results.manifest(run_id, *, user_context)` | `ResultManifest` | `request_id`, `owner` (the asker's `tenant_id` and `user_id`, and an anonymous asker's sorted `roles`) and `results: List[ResultSummary]`, the same entries as `QueryResult.result_manifest`. |
| `results.page(run_id, result_id, offset=0, limit=100, *, user_context)` | `ResultPage` | Rows `offset` to `offset + limit` of the result whose `node_id` is `result_id`. `limit` above `MAX_RESULT_PAGE_SIZE` (1,000) is cut to it, and the page's `limit` says so. A page past the end has no rows. `next_offset` is where the next page starts, `None` at the end. A negative `offset` or a `limit` below 1 raises `ValueError`. |
| `results.csv(run_id, result_id, *, user_context)` | `Iterator[bytes]` | The whole result as UTF-8 CSV, header first, one chunk per slice. The ids are checked before the iterator is returned, so a missing result raises before any byte is sent. |

`ResultPage` has `request_id`, `result_id`, `columns`, `rows` (`rows[i]` is row
`offset + i`), `offset`, `limit`, `total_rows` and `next_offset`.

Scope: a run is readable only by the caller who asked it, passed as the
required `user_context` ([Who can read a run](results.md#who-can-read-a-run));
no caller is a `TypeError`. Results are read from the engine's own `TENANT_ID`
folder, the tenant every run of this engine writes under. Each of these raises
`ResultNotFound` (a `LookupError`, exported from `nl2sql`): an unknown `run_id`,
a run another user or another tenant asked, a manifest that records no asker,
a `result_id` the run's manifest does not list, and an id that is not a plain
token (letters, digits, `_`, `-`; at most 128 characters). Ids are checked
before any path is built, and the parquet path is rendered from the artifact
template, never read from the manifest, so no id reaches a file outside the
run's folder.

```python
ann = UserContext(user_id="ann", roles=["admin"])
result = engine.run_query("List every genre.", user_context=ann)
[saved] = result.result_manifest                   # node_id, columns, row_count, truncated
page = engine.results.page(result.trace_id, saved.node_id, offset=0, limit=20, user_context=ann)
with open("genres.csv", "wb") as out:
    for chunk in engine.results.csv(result.trace_id, saved.node_id, user_context=ann):
        out.write(chunk)
```

`nl2sql-api` serves these as
[`GET /api/v1/query/{request_id}/results/{result_id}`](../rest/query.md#get-apiv1queryrequest_idresultsresult_id)
and `.../{result_id}.csv`; the playground serves the same two under `/api/query/...`.

### SubQueryResult

| name | type | required | meaning |
| --- | --- | --- | --- |
| `id` | `str` | no | Sub-query identifier. |
| `intent` | `str` | no | Semantic intent of the sub-query. |
| `sql` | `str` | no | SQL generated for the sub-query (`subgraph_outputs[<id>].sql_draft`). |
| `datasource_id` | `str` | no | Datasource the sub-query targets. |
| `schema_version` | `str` | no | Schema version used for planning. |
| `plan` | `Optional[Dict[str, Any]]` | no | The validated `PlanModel`, dumped. |
| `validation` | `List[ValidationCheck]` | no | `name`, `passed`, `message` per validation gate. |
| `rows` | `Optional[RowSample]` | no | Capped row sample; `None` when nothing executed or the artifact could not be read. |
| `status` | `str` | no | `"success"` or `"error"` for this sub-query, from its final attempt: `"success"` when it ended with SQL and, if executed, a result; otherwise `"error"`. A retry that recovers reports `"success"`. `"dropped"` when a [hook](../../configuration/hooks.md) denied it with `scope="sub_query"`: the run went on without it. |
| `retry_count` | `int` | no | Plan/SQL refinement attempts made. |
| `plan_source` | `"llm" \| "cache" \| "verified" \| "supplied" \| "approved" \| "chosen"` | no | `"cache"` when the plan came from the plan cache (no planner LLM call; still validated, generated and executed), `"verified"` when a verified query answered (no plan; `sql` is the verified query's own), `"supplied"` for a plan passed as `run_query(plan=...)`, `"approved"` for an approval's pinned plan (`"chosen"` is reserved for Wave B), otherwise `"llm"`. Only an `"llm"` plan is ever cached. See [Determinism → The plan cache](../../architecture/determinism.md#the-plan-cache-determinism-from-the-architecture). |
| `row_cap` | `Optional[int]` | no | The row limit that bounded this sub-query's SQL (the datasource's `row_limit`, bounded by `RESULT_ROW_LIMIT`), or `None` when the plan's own `LIMIT` was smaller. |
| `truncated` | `bool` | no | True when the result has `row_cap` rows: it was cut at the limit and may be missing rows. A result of exactly `row_cap` rows may be whole; it is counted as cut. |
| `knowledge_used` | `Optional[KnowledgeUsed]` | no | The glossary terms and rules this sub-query's prompts carried, and what the block's cap dropped. Only items within the caller's scope: none names a table the caller cannot read. Filled by the schema retriever (Wave B's K2); `None` when no term or rule matched, and for a plan served from the plan cache, which sent no prompt. |
| `estimate` | `Optional[Estimate]` | no | The database's estimate of the rows this sub-query reads, when the cost rule asked for one. `rows` is what the query is expected to *read*; `cost` is the database's own units, for display. `None` when the cost rule asked for none: no `policies.cost` limit applies to the caller, or the run did not reach `execute.before`. Present too when the rule then refused or asked for approval. |

### RowSample

| name | type | required | meaning |
| --- | --- | --- | --- |
| `columns` | `List[str]` | no | Column names, in result order. |
| `rows` | `List[List[Any]]` | no | At most `sample_rows` rows (default 50). |
| `total_rows` | `int` | no | Row count of the whole result, not of the sample. |

### State mapping

`result_from_state` normalises the raw graph state (LangGraph may return dicts or
model instances for nested values):

| `QueryResult` field | Graph state source |
| --- | --- |
| `sub_queries[].sql` | `subgraph_outputs[<id>].sql_draft` |
| `sub_queries[].id` / `.intent` / `.datasource_id` / `.schema_version` | `subgraph_outputs[<id>].sub_query` |
| `sub_queries[].plan` / `.validation` / `.status` / `.retry_count` / `.plan_source` / `.row_cap` / `.truncated` | `subgraph_outputs[<id>]` |
| `usage.plan_cache_hits` | count of `sub_queries[]` with `plan_source == "cache"` |
| `sub_queries[].rows` | `artifact_store.read_result_frame(subgraph_outputs[<id>].artifact)` |
| `final_answer` | `answer_synthesizer_response.final_answer` |
| `source` | `verified_match` (set by the curated matcher on a verified answer; absent means `generated`) |
| `errors`, `reasoning`, `warnings`, `trace_id`, `artifact_refs`, `timings`, `usage` | top-level state |

`errors` are projected onto a client-safe summary; `stack_trace` and `details` are
deliberately not exposed.

`result_from_state(state, artifact_store=None, sample_rows=50)` reads rows only
when an artifact store is supplied. A failed read is not fatal: `rows` stays
`None` and a warning is appended.

`timings` is collected by `NodeTimingCallback`
(`packages/nl2sql/src/nl2sql/pipeline/timing.py`), a LangChain callback handler
that `run_with_graph` always attaches. It keys on `metadata["langgraph_node"]`
where LangGraph provides it, so the keys are node names such as `ast_planner`,
`logical_validator`, `generator` and `executor`.

### Usage: tokens, calls and model time

`usage` is collected by `TokenUsageCallback`
(`packages/nl2sql/src/nl2sql/services/callbacks/token_handler.py`), which
`run_with_graph` attaches to every run, so the Python API, the REST API, the CLI
and the demo playground all report it. It is also filled in, with whatever had
been recorded, on a timed-out or cancelled run.

`QuestionUsage`:

| name | type | meaning |
| --- | --- | --- |
| `total` | `UsageTotals` | The whole question. |
| `nodes` | `Dict[str, UsageTotals]` | Keyed by graph node: `datasource_resolver` (the answerability check), `decomposer`, `ast_planner`, `refiner`, `answer_synthesizer`. |
| `calls` | `List[LLMCallUsage]` | Every model call in order, with its `node` and `model`. |
| `plan_cache_hits` | `int` | Sub-queries whose plan came from the plan cache. A hit makes no planner call, so it adds no `ast_planner` calls or tokens; a question answered entirely from the cache has no `ast_planner` entry in `nodes`. |

`UsageTotals`:

| name | type | meaning |
| --- | --- | --- |
| `calls` | `int` | LLM calls. The planner and refiner run once per retry, so a sub-query retried once shows `ast_planner.calls == 2`. |
| `input_tokens` | `Optional[int]` | Prompt tokens, including cached ones. |
| `cached_input_tokens` | `Optional[int]` | Input tokens served from the provider's prompt cache (a subset of `input_tokens`). |
| `cache_write_input_tokens` | `Optional[int]` | Input tokens written to the prompt cache (Anthropic reports these; OpenAI does not, so it is `null` there). |
| `output_tokens` | `Optional[int]` | Completion tokens, including reasoning ones. |
| `reasoning_tokens` | `Optional[int]` | Reasoning ("thinking") tokens (a subset of `output_tokens`). |
| `total_tokens` | `Optional[int]` | As the provider reports it; `input + output` if it does not. |
| `latency_s` | `float` | Seconds spent waiting on the model, summed over calls. The node's wall-clock time, which includes its non-LLM work, is in `timings`. |
| `cost` | `Optional[float]` | Dollars, when every call counted here has a cost; otherwise `null`. Each call is priced from `llm.prices`, then the dated price table: cache reads at `cached_input`, cache writes at `cache_write` (Claude's are 1.25x `input`), the rest of `input_tokens` at `input` and `output_tokens` at `output`; see [Prices](../../configuration/llm.md#prices). |

A token count in `UsageTotals` is the sum of what the calls reported, and
`null` when no call reported it: an unknown count is never `0`.

`LLMCallUsage` has the same token fields plus `node`, `model` (the model the
provider says served the call, e.g. `gpt-4o-2024-08-06`), `latency_s`, `cost`,
`usage_reported` and `error`.

Tokens are read by the adapter of the wire type that served the call
(`read_usage` in `nl2sql/llm/wires/openai.py` or `anthropic.py`), picked by the
`model_provider` the client stamps on the response; `TokenUsageCallback` only
asks it. Both read LangChain's `AIMessage.usage_metadata`
(`input_token_details.cache_read`, `input_token_details.cache_creation`,
`output_token_details.reasoning`) into the same fields, and differ where their
providers differ, as below. A detail the provider did not report is `null`, never `0`. A call whose result carried no usage
at all is recorded with every count `null` and `usage_reported: false`; a failed call is
recorded the same way, with its latency and `error`.

For OpenAI through `langchain-openai` (verified against the pinned 1.6.x by
`packages/nl2sql/tests/unit/test_fake_llm_server.py`): `prompt_tokens`,
`completion_tokens`, `total_tokens`, `prompt_tokens_details.cached_tokens` and
`completion_tokens_details.reasoning_tokens` all arrive. OpenAI's chat
completions API reports no cache-write count, so `cache_write_input_tokens` is
`null` there.

For Claude through `langchain-anthropic` (`provider: anthropic`; verified
against the fake Anthropic endpoint by
`packages/nl2sql/tests/unit/test_llm_wire_contract.py`, which runs the same
usage tests against both wires), Anthropic's `usage` maps
as follows:

| Anthropic `usage` | field here |
| --- | --- |
| `cache_read_input_tokens` | `cached_input_tokens` |
| `cache_creation_input_tokens`, or its per-TTL split `cache_creation.ephemeral_5m_input_tokens` + `ephemeral_1h_input_tokens` | `cache_write_input_tokens` |
| `input_tokens` + `cache_read_input_tokens` + `cache_creation_input_tokens` | `input_tokens` |
| `output_tokens` | `output_tokens` |

Anthropic's own `input_tokens` counts only the uncached tail of the prompt;
`langchain-anthropic` adds the cache reads and writes back, so `input_tokens`
here is the whole prompt, counted once, and both cache fields stay subsets of
it. When the write is reported per TTL, `langchain-anthropic` sets the generic
`cache_creation` detail to `0` and puts the tokens under the TTL keys, which
are summed here.

Cost is `(input - cached - cache_write) * input_price + cached * cached_input_price
+ cache_write * cache_write_price + output * output_price`, per million tokens; a
cache count the provider did not report is not split out of `input`. Prices are
looked up in `llm.prices` by the served model name, then by the configured one,
and only then in the dated table by either, by exact match only, so a `gpt-4o`
price never prices `gpt-4o-mini`. A call with no price, or no input or output count, has a
`null` cost. See [Prices](../../configuration/llm.md#prices).
