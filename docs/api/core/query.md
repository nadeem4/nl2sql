# Query API

## Purpose
Execute a natural language query using the NL2SQL pipeline.

## Responsibilities
- Invoke pipeline graph runtime.
- Return a structured `QueryResult`.

## Key Modules
- `packages/nl2sql/src/nl2sql/api/query_api.py`
- `packages/nl2sql/src/nl2sql/pipeline/runtime.py`
- `packages/nl2sql/src/nl2sql/pipeline/graph.py`
- `packages/nl2sql/src/nl2sql/pipeline/state.py`

## Public Surface

### QueryAPI.run_query

Source:
`packages/nl2sql/src/nl2sql/api/query_api.py`

Signature:
`run_query(natural_language: str, datasource_id: Optional[str] = None, execute: bool = True, user_context: Optional[UserContext] = None) -> QueryResult`

`run_query` passes the context's artifact store to `result_from_state`, so each
sub-query carries a capped row sample alongside its artifact reference.

Parameters:
| name | type | required | meaning |
| --- | --- | --- | --- |
| `natural_language` | `str` | yes | User query. |
| `datasource_id` | `Optional[str]` | no | Datasource override; otherwise resolved. |
| `execute` | `bool` | no | Whether to execute SQL against datasource. |
| `user_context` | `Optional[UserContext]` | no | RBAC context. |

Returns:
`QueryResult`, built from the pipeline graph state by `result_from_state`.

Raises:
No exceptions are raised by `run_query`; errors are returned in `QueryResult.errors`.

Side Effects:
- Pipeline execution, LLM calls, optional database execution.

Idempotency:
- Not guaranteed; execution can depend on external systems and time.

## Execution Lifecycle
- Create a per-run `CancellationToken` and install signal / Ctrl+X handlers bound to it.
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
| `final_answer` | `Optional[Dict[str, Any]]` | no | Answer synthesizer payload (`summary`, `format_type`, `content`). |
| `errors` | `List[Dict[str, Any]]` | no | Pipeline errors (`node`, `message`, `error_code`, `severity`). |
| `trace_id` | `str` | no | Trace identifier. |
| `reasoning` | `List[Dict[str, Any]]` | no | Reasoning events/logs. |
| `warnings` | `List[Dict[str, Any]]` | no | Warning events/logs. |
| `artifact_refs` | `Dict[str, ArtifactRef]` | no | Result artifact references keyed by execution node id. |
| `status` | `str` | no | `"success"`, `"error"`, `"plan_only"`, or `""` when nothing ran. |
| `timings` | `Dict[str, float]` | no | Wall-clock seconds per graph node, plus `LangGraph` for the whole run. |
| `usage` | `QuestionUsage` | no | LLM calls, tokens and model time per node and for the whole question. See below. |
| `trace_path` | `Optional[str]` | no | Where this run's trace file was written, or `None` when `TRACE_MODE` did not write one. See [Debugging a Run](../../observability/debugging.md). |

`errors` holds only `ERROR` and `CRITICAL` entries; `WARNING`-severity pipeline
errors are appended to `warnings` as the same summary dict.

A sub-query that succeeded contributes nothing to `errors`: the errors of the
attempts it recovered from, and its warnings, are in `warnings` with their
original `severity` and a `sub_query_id`. The run trace and
`subgraph_outputs[<id>].errors` keep every attempt's errors for debugging. A
sub-query that failed without a blocking error of its own (it ended with no SQL
on warnings alone) adds a `MISSING_SQL` error, so the run reports `"error"`.

`status` is derived: `"error"` when any blocking error is present, otherwise
`"plan_only"` when no sub-query produced rows or an artifact (an `execute=False`
run), otherwise `"success"`. A state with no sub-queries and no errors leaves it
empty.

Only a capped sample of the rows is inlined, in `sub_queries[].rows`. The full
result set lives in artifact storage, addressed through `artifact_refs`.

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
| `status` | `str` | no | `"success"` or `"error"` for this sub-query, from its final attempt: `"success"` when it ended with SQL and, if executed, a result; otherwise `"error"`. A retry that recovers reports `"success"`. |
| `retry_count` | `int` | no | Plan/SQL refinement attempts made. |
| `plan_source` | `str` | no | `"cache"` when the plan came from the plan cache (no planner LLM call; still validated, generated and executed), otherwise `"llm"`. See [Determinism → The plan cache](../../architecture/determinism.md#the-plan-cache-determinism-from-the-architecture). |

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
| `sub_queries[].plan` / `.validation` / `.status` / `.retry_count` / `.plan_source` | `subgraph_outputs[<id>]` |
| `usage.plan_cache_hits` | count of `sub_queries[]` with `plan_source == "cache"` |
| `sub_queries[].rows` | `artifact_store.read_result_frame(subgraph_outputs[<id>].artifact)` |
| `final_answer` | `answer_synthesizer_response.final_answer` |
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
| `input_tokens` | `int` | Prompt tokens, including cached ones. |
| `cached_input_tokens` | `int` | Input tokens served from the provider's prompt cache (a subset of `input_tokens`). |
| `cache_write_input_tokens` | `int` | Input tokens written to the prompt cache (Anthropic reports these; OpenAI does not). |
| `output_tokens` | `int` | Completion tokens, including reasoning ones. |
| `reasoning_tokens` | `int` | Reasoning ("thinking") tokens (a subset of `output_tokens`). |
| `total_tokens` | `int` | As the provider reports it; `input + output` if it does not. |
| `latency_s` | `float` | Seconds spent waiting on the model, summed over calls. The node's wall-clock time, which includes its non-LLM work, is in `timings`. |
| `cost` | `Optional[float]` | Only when `LLM_PRICES` prices every call counted here; otherwise `null`. |

`LLMCallUsage` has the same token fields plus `node`, `model` (the model the
provider says served the call, e.g. `gpt-4o-2024-08-06`), `latency_s`, `cost`,
`usage_reported` and `error`.

Tokens are read by the adapter of the wire type that served the call
(`read_usage` in `nl2sql/llm/wires/openai.py` or `anthropic.py`), picked by the
`model_provider` the client stamps on the response; `TokenUsageCallback` only
asks it. Both read LangChain's `AIMessage.usage_metadata`
(`input_token_details.cache_read`, `input_token_details.cache_creation`,
`output_token_details.reasoning`) into the same fields, and differ where their
providers differ, as below. A detail the provider did not report is `0`. A call whose result carried no usage
at all is recorded with zero tokens and `usage_reported: false`; a failed call is
recorded with its latency and `error`.

For OpenAI through `langchain-openai` (verified against the pinned 1.6.x by
`packages/nl2sql/tests/unit/test_fake_llm_server.py`): `prompt_tokens`,
`completion_tokens`, `total_tokens`, `prompt_tokens_details.cached_tokens` and
`completion_tokens_details.reasoning_tokens` all arrive. OpenAI's chat
completions API reports no cache-write count, so `cache_write_input_tokens` is
`0` there.

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

Cost is `(input - cached) * input_price + cached * cached_input_price + output * output_price`,
per million tokens. Prices are looked up by the served model name, then by the
configured one, by exact match only, so a `gpt-4o` price never prices
`gpt-4o-mini`. Cache writes are charged at the input price.
