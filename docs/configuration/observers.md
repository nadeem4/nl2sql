# Observers

An observer receives the engine's [events](../observability/events.md): every
run's steps, model calls, policy and hook decisions, and the engine's own
events (an LLM swap, a purge). Where they go is the `observers` section of
`nl2sql.yaml`: a log, an audit trail, an OpenTelemetry collector, or a plugin
of your own.

```yaml
# nl2sql.yaml
observers: observers.yaml

# observers.yaml
- type: log
  level: INFO                     # node.started and answer.delta are logged only at DEBUG
- type: audit
  max_age_days: 90
- type: otel                      # needs nl2sql-engine[otel]
  endpoint: http://collector:4317
- type: corp_siem                 # a plugin; its config_model checks the rest
  url: "${env:SIEM_URL}"
```

```python
from nl2sql import NL2SQL, ObserverConfig

engine = NL2SQL(config.model_copy(update={"observers": [ObserverConfig(type="log"), MyObserver()]}))
```

| Rule | Detail |
| --- | --- |
| No section | The engine runs `log` and `audit`. `observers: []` runs none. |
| Order | Observers are subscribed in the section's order and receive each event in that order. |
| Validation | `type` names a registered observer (`nl2sql.observers`), with "did you mean" for a typo. The observer's `config_model` checks the other keys when the engine is built; a `${provider:key}` reference is resolved then. Every problem is in the one `ConfigError`. |
| Objects | An object with `on_event` in the list is used as given. |
| Closing | `engine.close()` closes every observer, which writes what it still holds. |

## Observers fail open and never block

An exception from an observer is logged once per observer and event name, at
WARNING on logger `nl2sql.events`, and skipped; it never fails a run. The
bus counts it (`EventBus.failures`).

`on_event` is called on the thread that published the event, which may be one
of a run's worker threads, and a run's events are delivered one at a time so
that `run.finished` is always its last. So an observer must return quickly:
anything slow (a network export, a disk write) belongs on a queue of its own.
The `audit` observer writes on its own thread, and `otel` records in memory
while the OpenTelemetry SDK exports in its background thread.

## `log`

One line per event on logger `nl2sql.events`, at `level`: the event's name and
a short summary of its data (`run.finished status=success duration_s=1.2
...`). Under DEBUG it logs every event; at any other level it leaves out
`node.started` and `answer.delta`, one line per step and one per token.

| Key | Default | Meaning |
| --- | --- | --- |
| `level` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `events` | every event but `node.started` and `answer.delta`; every event at `DEBUG` | The names to log; each must be in the catalog |

The run's `trace_id` and `tenant_id` reach the line through the context, as
they reach every line the engine writes during a run; with
`nl2sql --log-format json` each line carries them as fields.

## `audit`

JSON lines in `<storage.path>/audit/<date>.jsonl`, one file per UTC day. Each
line is the event as JSON: `name`, `at`, `trace_id`, `tenant_id` and `data`.

| Written | Why |
| --- | --- |
| `run.finished`, `run.failed` | One summary line per run: the user, roles, datasources, tables read (`datasource.schema.table`), status and source. Never rows. "Who read `payroll.salaries`" is a search for that table. |
| `policy.decision` | A denial at an enforcement point |
| `hook.decision` | Only `deny`, `needs_approval`, `approved` and `error`; an `allow` or `modify` is not |
| `run.limit_reached` | A question stopped at its cost or call cap |
| `llm.changed` | `engine.llm.replace`: who, and each agent's old and new provider and model; never a key |
| `index.swapped`, `schema.drifted`, `retention.purged` | Changes to the index and to what is kept |

The lines are written on the observer's own thread. Its queue holds up to
10,000 lines: when the disk cannot keep up, a line is dropped rather than
holding a run, counted in `AuditObserver.dropped`, and warned about once.
`engine.close()` writes what is queued, and so does the process's exit when
nothing closed the engine (a script or a notebook).

The list is the [event catalog's](../observability/events.md#the-catalog):
an event is audited when its catalog entry says so (`EventInfo.audit`), and
`audit_only` narrows it to some values of a field (`hook.decision`'s
`decision`). An event added to the catalog later joins the log through its
own entry.

| Key | Default | Meaning |
| --- | --- | --- |
| `max_age_days` | `90` | At least 1. `engine.storage.purge()` (and `nl2sql storage purge`, and `nl2sql-api` once a day) deletes the day files older than this, and reports how many as `audit` |

## `otel`

OpenTelemetry metrics and traces, from the bus. Install the extra:

```bash
pip install "nl2sql-engine[otel]"
```

Without it, an `otel` observer is a `ConfigError` naming that command. Only
this observer imports `opentelemetry`, and only when it is built.

| Metric | Kind | Attributes | From |
| --- | --- | --- | --- |
| `nl2sql.node.duration` | histogram, `s` | `node` | `node.finished` |
| `nl2sql.token.usage` | counter | `node`, `model`, `type` (`input`, `cached_input`, `cache_write_input`, `output`, `reasoning`, `total`) | `llm.call` |
| `nl2sql.plan_cache.lookups` | counter | `result` (`hit`, `miss`), `datasource_id` | `plan_cache.lookup` |
| `nl2sql.run.cost` | histogram, `USD` | `status` | `run.finished`, when the run was priced |

Traces: one span per run, `nl2sql.run`, from `run.started` to `run.finished`
or `run.failed` (an error status), with the attributes `nl2sql.trace_id`,
`nl2sql.status`, `nl2sql.source` and `nl2sql.datasources`; and a child span per
step, `nl2sql.node.<node>`, with `nl2sql.node` and `nl2sql.sub_query_id`.

The observer makes its own `MeterProvider` and `TracerProvider` and never sets
OpenTelemetry's process-wide ones, so two engines in one process can export to
two collectors. `engine.close()` gives each provider 5 seconds to flush, so a
collector that is down does not hold the shutdown longer; what was not sent by
then is dropped.

| Key | Default | Meaning |
| --- | --- | --- |
| `endpoint` | `http://localhost:4317` | The OTLP collector (gRPC) |
| `exporter` | `otlp` | `otlp`, or `console` to print to stdout |
| `metrics` | `true` | Record and export the metrics |
| `traces` | `true` | Record and export the spans |
| `service_name` | `nl2sql` | The `service.name` resource attribute |

## Writing an observer

An observer depends on the SDK alone (`nl2sql_sdk.events`). Declare it under
the `nl2sql.observers` entry point group of your package:

```python
from pathlib import Path
from typing import ClassVar, FrozenSet, Optional

from pydantic import BaseModel
from nl2sql_sdk.events import Event


class SiemConfig(BaseModel):
    url: str


class SiemObserver:
    interface_version: ClassVar[int] = 1
    config_model: ClassVar[type[BaseModel]] = SiemConfig
    events: ClassVar[Optional[FrozenSet[str]]] = frozenset({"run.finished", "policy.decision"})

    @classmethod
    def from_config(cls, cfg: SiemConfig, *, folder: Path) -> "SiemObserver":
        return cls(cfg.url)            # folder is storage.path, for anything it writes

    def __init__(self, url: str) -> None:
        self.url = url

    def on_event(self, event: Event) -> None:
        ...                            # put it on a queue; never block

    def close(self) -> None:
        ...                            # flush the queue
```

```toml
[project.entry-points."nl2sql.observers"]
corp_siem = "corp_siem:SiemObserver"
```

`engine.events.catalog()` lists every event with its data's JSON Schema; an
event never carries result rows (see the invariant
[Events Carry No Rows](../architecture/invariants.md)).
