# Events

Everything the engine does is published as an **event** on its event bus: each
step of a question, its result manifest, its answer as it is written, and how
it ended; and, as Wave A lands, model calls, policy denials, hook decisions,
index builds, purges and LLM swaps. Two readers see them:

- **The caller who asked the question** sees their own run's events on
  `engine.stream_query`, from a caller-safe allow-list. `nl2sql-api`'s
  `POST /query/stream`, the playground and `nl2sql run` all read this stream.
- **An operator** sees every event of every run, and the engine's own, through
  `engine.events.subscribe`.

The design is [Wave A, section 3](../architecture/wave_a.md#3-the-event-bus).

## The event

```python
from nl2sql import Event   # nl2sql_sdk.events.Event: pydantic and the standard library only

event.name       # "node.finished", from the catalog
event.at         # when, in UTC
event.trace_id   # the run's id (QueryResult.trace_id); None for an engine event no run caused
event.tenant_id
event.data       # JSON-ready; the catalog's model for this name
event.sse()      # "event: <name>\ndata: <the whole event as JSON>\n\n"
```

`trace_id` is the id a run has everywhere: the result
(`QueryResult.trace_id`), its saved results (`engine.results.page`), its trace
(`engine.traces.get`) and its log lines all join on it.

No event carries result rows, sample values, keys or header values. The
question and the answer text are carried, and may contain values from the
data.

## The catalog

`engine.events.catalog()` lists every event: its `name`, `description`,
whether a run has it (`run`), whether the caller's stream carries it
(`stream`), whether the audit observer writes it (`audit`) and its data's JSON
Schema (`data_schema`). The catalog lives in `nl2sql/events/catalog.py`; a name
that is not in it cannot be published.

Run events (they carry the run's `trace_id`):

| Name | Data | On the stream |
| --- | --- | --- |
| `run.started` | `question`, `user_id`, `tenant_id`, `roles`, `datasource_id`, `execute` | yes |
| `node.started` | `node`, `sub_query_id` | yes |
| `node.finished` | `node`, `sub_query_id`, `seconds`, `ok` | yes |
| `answer.delta` | `field` (`summary`, `content`), `text` | yes |
| `result.manifest` | `results`: each saved result's `node_id`, `columns`, `row_count`, `truncated` | yes |
| `sanity.flagged` | `code`, `sub_query_id`, `retry` | yes |
| `run.limit_reached` | `limit`, `value`, `spent` | yes |
| `run.finished` | a summary, never rows: `status`, `duration_s`, `usage`, `error_codes`, `source`, `datasources`, `tables` (`datasource.schema.table`), `result_manifest`, `user_id`, `roles` | yes, with `result` added |
| `run.failed` | `message`, `error_code` | yes |
| `llm.call` | the model, its tokens, latency and cost | no |
| `llm.parse_retry` | `agent`, `schema`, `attempt`, `errors` | no |
| `plan_cache.lookup` | `datasource_id`, `hit` | no |
| `policy.decision` | a denial: `datasource_id`, `schema`, `table`, `rule`, `step` | no |
| `hook.decision` | `hook`, `point`, `decision`, `reason`, ... | no |

Engine events (`datasource.failed`, `index.started`, `index.column_failed`,
`index.swapped`, `index.failed`, `schema.drifted`, `retention.purged`,
`llm.changed`, `eval.case`, `eval.finished`) carry no `trace_id`. Which PR
of Wave A starts publishing each one is in its description; `node.*`,
`answer.delta`, `result.manifest` and the three `run.*` events are published
today.

## The run's stream

```python
from nl2sql import NL2SQL, UserContext

engine = NL2SQL.from_config("nl2sql.yaml")
for event in engine.stream_query("Which genre sells the most tracks?",
                                 user_context=UserContext(roles=["analyst"])):
    if event.name == "node.started":
        print("running", event.data["node"])
    elif event.name == "answer.delta":
        print(event.data["text"], end="", flush=True)
    elif event.name == "run.finished":
        result = event.data["result"]   # what run_query returns, as JSON
    elif event.name == "run.failed":
        print("could not run:", event.data["message"])
```

The stream yields its own run's events, in the order they were published,
from the allow-list: `run.started`, `node.started`, `node.finished`,
`answer.delta`, `result.manifest`, `sanity.flagged`, `run.limit_reached`, and
then exactly one of `run.finished` or `run.failed`.

- **The stream is the caller's, so it carries only what the caller may see.**
  A `policy.decision` would tell a denied user which table exists, and
  `hook.decision`, `llm.call` and `plan_cache.lookup` are operator data. They
  reach an operator through `engine.events.subscribe` only.
- **The terminal event holds the result, on the stream only.** The stream's
  `run.finished` is the bus event's summary plus `data.result`, the
  `QueryResult` as JSON. The event published on the bus never has it.
- **A failure inside the pipeline is still `run.finished`.** A refusal, a
  provider error, a timeout or a cancel ends in `run.finished` with
  `status: "error"`, exactly as `run_query` returns it. `run.failed` means the
  run could not start or crashed outside the pipeline.
- **Exactly one terminal event, whatever breaks.** A failure the run's own
  `run.failed` could not report (an interrupt, a crash after the run's
  summary was published) still ends the stream with a `run.failed` carrying
  the run's `trace_id`.
- Closing the iterator early cancels the run before its next step or model
  call.

As Server-Sent Events, each event is `event: <name>` and one `data:` line
holding the whole event as JSON. That is what `nl2sql-api`'s
`POST /api/v1/query/stream` ([REST](../api/rest/query.md)) and the
playground's `/api/ask/stream` send.

The allow-list is one definition: the catalog entries marked `stream`
(`nl2sql.events.STREAM_EVENTS`). An event a later release adds joins the
stream by its catalog entry alone.

## Subscribing: the operator's view

```python
def show(event):
    print(event.at, event.trace_id, event.name, event.data)

subscription = engine.events.subscribe(show)                         # every event
denials = engine.events.subscribe(show, names=["policy.decision"])   # some
...
subscription.close()
```

`engine.events.subscribe(fn, names=None)` calls `fn` for every event the
engine publishes, every run's and the engine's own, or for `names` only (a
name not in the catalog is a `ValueError`). `fn` is a function of one `Event`,
or an `Observer` (`nl2sql.Observer`): an object with `on_event`, whose own
`events` choose its names when `names` is not given. `Subscription.close()`
detaches it.

- **Synchronous.** `fn` is called on the thread that published the event,
  which may be one of a run's worker threads, before the run goes on. A slow
  subscriber slows the run; an observer that keeps state locks it.
- **Fails open.** An exception from `fn` is caught and logged once per
  subscriber and event name, at WARNING, on the `nl2sql.events` logger; it
  never fails a run.
- **One bus per engine.** Two engines in one process share no subscriber.

## Logs carry the run's `trace_id`

Every log line written while a question runs carries its `trace_id` and
`tenant_id`, on the caller's thread and on every graph worker thread, so a
trace, its events and its log lines join on one id. `nl2sql`, `nl2sql demo` and
`nl2sql-api` configure logging with the filter that adds them
(`--log-format json`, or `NL2SQL_LOG_FORMAT=json`, writes them as fields). A
library caller who configures logging their own way adds
`nl2sql.TraceContextFilter` to their handler:

```python
import logging
from nl2sql import TraceContextFilter

handler = logging.StreamHandler()
handler.addFilter(TraceContextFilter())
handler.setFormatter(logging.Formatter("%(asctime)s [%(trace_id)s] %(name)s %(message)s"))
logging.getLogger().addHandler(handler)
```

## The trace

A run's [trace](debugging.md) lists its events (every one but
`answer.delta`) under `events`.
